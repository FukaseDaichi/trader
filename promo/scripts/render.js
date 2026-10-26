// Frame-accurate renderer: seeks the Motion timeline frame by frame and
// pipes screenshots into ffmpeg.  Usage:
//   node promo/scripts/render.js [out.mp4] [--fps 30] [--workers 4] [--only t1,t2,...]
const { chromium } = require("playwright");
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const out = path.resolve(args[0] && !args[0].startsWith("--") ? args[0] : "promo/ai-stock-trader.mp4");
const fps = +opt("--fps", 30);
const workers = +opt("--workers", 4);
const only = opt("--only", null);
const page = "file://" + path.resolve(__dirname, "..", "index.html") + "?render";

async function openPage(browser) {
  const p = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  await p.route(/fonts\.(googleapis|gstatic)\.com/, r => r.abort());
  await p.goto(page);
  await p.waitForFunction(() => window.__ready === true);
  await p.evaluate(() => document.fonts.ready);
  return p;
}

(async () => {
  const browser = await chromium.launch();
  if (only) {
    const p = await openPage(browser);
    for (const t of only.split(",").map(Number)) {
      await p.evaluate(t => window.__seek(t), t);
      await p.screenshot({ path: path.join(path.dirname(out), `still_${t}.png`) });
    }
    await browser.close();
    return;
  }
  const dur = await (await openPage(browser)).evaluate(() => window.__duration);
  const total = Math.round(dur * fps);
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "frames-"));
  let done = 0;
  const work = async w => {
    const p = await openPage(browser);
    for (let f = w; f < total; f += workers) {
      await p.evaluate(t => window.__seek(t), f / fps);
      await p.screenshot({ path: path.join(tmp, `f${String(f).padStart(5, "0")}.jpg`), type: "jpeg", quality: 95 });
      if (++done % 150 === 0) console.log(`${done}/${total}`);
    }
  };
  await Promise.all([...Array(workers).keys()].map(work));
  await browser.close();
  await new Promise((res, rej) => {
    const ff = spawn("ffmpeg", ["-y", "-loglevel", "error", "-framerate", String(fps), "-i", path.join(tmp, "f%05d.jpg"),
      "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out], { stdio: "inherit" });
    ff.on("exit", c => (c === 0 ? res() : rej(new Error("ffmpeg " + c))));
  });
  fs.rmSync(tmp, { recursive: true, force: true });
  console.log("wrote", out);
})();
