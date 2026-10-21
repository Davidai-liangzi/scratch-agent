// 把素材（PNG / SVG）转成小缩略图，供视觉模型看。
// 用法：node thumb.js <jobs.json>
// jobs.json: [{ "src": "原始文件", "out": "输出 png" }]
const fs = require('fs');
const sharp = require('sharp');

async function main() {
  const jobs = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  for (const job of jobs) {
    try {
      await sharp(job.src, { density: 120 })
        .resize(128, 128, { fit: 'contain', background: { r: 0, g: 0, b: 0, alpha: 0 } })
        .png()
        .toFile(job.out);
    } catch (e) {
      // 单个素材渲染失败就跳过，不影响其他候选
    }
  }
}

main().catch(() => process.exit(0));
