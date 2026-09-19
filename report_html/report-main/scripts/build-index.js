const fs = require("fs");
const path = require("path");

const rootDir = path.resolve(__dirname, "..");
const pageFiles = [
  "pages/01-cover.html",
  "pages/02-financial-story.html",
  "pages/03-dein-monat.html",
  "pages/04-clarity-score.html",
  "pages/05-wealth-journey.html",
  "pages/06-your-goal.html",
  "pages/07-money-map.html",
  "pages/08-meilensteine.html",
  "pages/09-clarity-recap.html",
  "pages/10-closing.html",
];

function readPageMarkup(filePath) {
  const absolutePath = path.join(rootDir, filePath);
  const html = fs.readFileSync(absolutePath, "utf8");
  const mainMatch = html.match(/<main\b[^>]*>([\s\S]*?)<\/main>/i);

  if (!mainMatch) {
    throw new Error(`${filePath} does not contain a <main> block.`);
  }

  return mainMatch[1].trimEnd().replaceAll('src="../imgs/', 'src="./imgs/');
}

const pagesMarkup = pageFiles.map(readPageMarkup).join("\n\n");
const indexHtml = `<!doctype html>
<html lang="de">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Clarity Report</title>
    <link rel="stylesheet" href="style.css" />
  </head>
  <body>
    <main class="report" aria-label="Clarity Monatsreport">
${pagesMarkup}
    </main>
  </body>
</html>
`;

fs.writeFileSync(path.join(rootDir, "index.html"), indexHtml);
console.log(`Built index.html from ${pageFiles.length} page files.`);
