const CHAPTER_HEADING_PATTERN = /^(?:\*{1,2}\s*)?(?:[#＃]\s*)?(?:[【\[(（]\s*)?第\s*[0-9零一二三四五六七八九十百千两]+\s*(?:章|回|节|幕)(?:\s*[：:、.．—-]\s*|\s+)?(?:.*?)(?:\s*[】\])）])?(?:\s*\*{1,2})?\s*$/u;

const testCases = [
  "第1章：三里屯的野猫",
  "**第1章：三里屯的野猫**",
  "第1章：三里屯的野猫  ",
  "第一章 标题",
  "第 1 章：标题"
];

for (const tc of testCases) {
  console.log(`"${tc}" ->`, CHAPTER_HEADING_PATTERN.test(tc));
}
