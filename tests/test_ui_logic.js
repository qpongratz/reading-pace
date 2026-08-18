/* Tests for the browser-side logic that has no DOM dependency.
 *
 *   node tests/test_ui_logic.js
 *
 * The functions live inside web/index.html rather than a module, so they are
 * extracted by name and evaluated here. That keeps the app a single file with
 * no build step, which is the point, while still letting the arithmetic be
 * tested rather than eyeballed.
 */

const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(
  path.join(__dirname, "..", "web", "index.html"), "utf8");
const js = html.slice(html.lastIndexOf("<script>") + 8,
                      html.lastIndexOf("</script>"));

/** Pull one top-level function or const-arrow out of the app source. */
function extract(name) {
  let i = js.indexOf(`function ${name}(`);
  if (i < 0) i = js.indexOf(`const ${name} =`);
  if (i < 0) throw new Error(`could not find ${name}`);
  let depth = 0, started = false;
  for (let j = i; j < js.length; j++) {
    if (js[j] === "{") { depth++; started = true; }
    else if (js[j] === "}") {
      depth--;
      if (started && depth === 0) return js.slice(i, j + 1);
    }
  }
  throw new Error(`unbalanced braces in ${name}`);
}

const src = ["const HUES = 8, TIERS = 3;",
             extract("paint"), extract("groupKey"), extract("assignColors"),
             extract("derivation")].join("\n");
const { paint, groupKey, assignColors, derivation } =
  new Function(src + "; return {paint, groupKey, assignColors, derivation};")();

let failures = 0;
function check(label, cond, detail) {
  if (!cond) failures++;
  console.log(`  ${cond ? "ok  " : "FAIL"} ${label}${detail ? "  — " + detail : ""}`);
}

console.log("colour assignment");
{
  const books = [];
  for (let i = 0; i < 26; i++) books.push({ series: "S" + i });
  const c = assignColors(books);
  check("every group gets a fill and a wash",
        c.length === 26 && c.every(x => x.fill && x.wash));
  check("group 1 takes slot 1", c[0].fill === "var(--s1)", c[0].fill);
  check("group 9 reuses hue 1 with 45° stripes",
        c[8].fill.includes("var(--s1)") && c[8].fill.includes("45deg"));
  check("group 17 reuses hue 1 with 135° stripes",
        c[16].fill.includes("var(--s1)") && c[16].fill.includes("135deg"));
  check("hues are never generated past the third tier",
        c[24].fill === "var(--s-other)", c[24].fill);
}

console.log("series grouping");
{
  const s = assignColors([{ series: "Sun Eater" }, { series: "Sun Eater" },
                          { series: "Sun Eater" }, { series: "Other" }]);
  check("a series keeps one hue across its volumes",
        s.slice(0, 3).every(x => x.fill.includes("--s1")));
  check("consecutive volumes alternate so neighbours stay separable",
        s[1].fill !== s[0].fill && s[0].fill === s[2].fill);
  check("a second series takes the next slot", s[3].fill.includes("--s2"));
}

console.log("grouping falls back to author when series is absent");
{
  const q = [
    { title: "Demon in White", author: "Christopher Ruocchio" },
    { title: "Kingdoms of Death", author: "Christopher Ruocchio" },
    { title: "This Inevitable Ruin", author: "Matt Dinniman" },
    { title: "Howling Dark", author: "Christopher Ruocchio", series: "Sun Eater" },
  ];
  const g = assignColors(q);
  check("same author shares a hue with no series metadata",
        g[0].fill.includes("--s1") && g[1].fill.includes("--s1"));
  check("a different author takes the next slot", g[2].fill.includes("--s2"));
  check("series still wins where it exists", groupKey(q[3]) === "s:sun eater");
  check("author key is used otherwise",
        groupKey(q[0]) === "a:christopher ruocchio");
  check("title is the last resort", groupKey({ title: "Orphan" }) === "b:orphan");
}

console.log("standalone books");
{
  const t = assignColors([{ title: "A" }, { title: "B" }, { title: "A" }]);
  check("repeats of one title share its hue", t[2].fill.includes("--s1"));
  check("but are stepped, like volumes of a series", t[2].fill !== t[0].fill);
  check("different titles get different slots", !t[1].fill.includes("--s1"));
}

console.log("derivation");
{
  global.STATE = { words_per_page: 365, calibrated: true };
  global.PACE = 1.0;
  const rows = derivation({
    pages: 100, words: 36500, words_source: "from pages",
    percent_done: 50, remaining_words: 18250, days: 3,
    rate: { wpm: 100, basis: "series", label: "Sun Eater", n: 2 },
  });
  check("five rows: length, progress, speed, budget, result",
        rows.length === 5, `got ${rows.length}`);
  check("converts pages to words", rows[0][1].includes("36,500"), rows[0][1]);
  check("subtracts what is already read", rows[1][1].includes("18,250"), rows[1][1]);
  check("names where the speed came from", rows[2][1].includes("Sun Eater"), rows[2][1]);
  check("words per day is wpm × 60 × pace",
        rows[3][1].includes("6,000"), rows[3][1]);
  check("last row is the division that gives the days",
        rows[4][0] === "3 days", rows[4][0]);

  global.STATE = { words_per_page: 365, calibrated: false };
  const r2 = derivation({
    words: 1000, words_source: "given", percent_done: 0,
    remaining_words: 1000, days: 1,
    rate: { wpm: 250, basis: "global", label: "typical", n: 1 },
  });
  check("says plainly when nothing has been measured",
        r2[1][1].includes("nothing measured"), r2[1][1]);
}

console.log(failures ? `\n${failures} failed` : "\nall passed");
process.exit(failures ? 1 : 0);
