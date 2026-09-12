import sharp from "sharp";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const output = path.join(root, "public/gibby");
const width = 520;
const height = 360;

async function frame(name, input, scale, pivotX, floorY, mirrored = true) {
  const metadata = await sharp(input).metadata();
  const resizedWidth = Math.round(metadata.width * scale);
  const resizedHeight = Math.round(metadata.height * scale);
  let image = sharp(input).resize(resizedWidth, resizedHeight, { kernel: "nearest" });
  if (mirrored) image = image.flop();
  const left = Math.round(110 - (mirrored ? metadata.width - pivotX : pivotX) * scale);
  const top = Math.round(height - floorY * scale);
  const buffer = await image.png().toBuffer();
  const cropped = await sharp(buffer).extract({
    left: Math.max(0, -left), top: Math.max(0, -top),
    width: Math.min(resizedWidth, width - left) - Math.max(0, -left),
    height: Math.min(resizedHeight, height - top) - Math.max(0, -top),
  }).png().toBuffer();
  await sharp({ create: { width, height, channels: 4, background: "#00000000" } })
    .composite([{ input: cropped, left: Math.max(0, left), top: Math.max(0, top) }])
    .png().toFile(path.join(output, name));
}

const ridingColumns = [0, 190, 358, 520, 681, 844, 997, 1173, 1355, 1536];
const ridingCenters = [98, 270, 440, 606, 771, 929, 1094, 1270, 1454];
for (let index = 0; index < 9; index++) {
  const left = ridingColumns[index];
  const input = await sharp(path.join(root, "UI-images/drone riding.png"))
    .extract({ left, top: 690, width: ridingColumns[index + 1] - left, height: 300 })
    .png().toBuffer();
  await frame(`drone-ride-${index + 1}.png`, input, 1.12, ridingCenters[index] - left, 289);
}

const boarding = [
  { scale: 1.05, pivot: 218, floor: 205 },
  { scale: 0.93, pivot: 103, floor: 219 },
  { scale: 1.04, pivot: 103, floor: 325 },
  { scale: 0.96, pivot: 115, floor: 276 },
];
for (let index = 0; index < boarding.length; index++) {
  const { scale, pivot, floor } = boarding[index];
  await frame(`drone-return-${index + 1}.png`,
    path.join(output, `drone-board-${index + 1}.png`), scale, pivot, floor);
}

// Follow the gap between the arm and rotor rather than cutting the rotor off.
const parkedMask = Buffer.from(`<svg width="311" height="216">
  <path fill="white" d="M145 108H311V216H124V134L145 119Z"/>
</svg>`);
const parked = await sharp(path.join(output, "drone-board-1.png"))
  .composite([{ input: parkedMask, blend: "dest-in" }]).png().toBuffer();
await frame("drone-parked.png", parked, 1.05, 218, 205);

const jumpColumns = [0, 176, 327, 500, 666, 856, 1024];
const jumpCenters = [97, 249, 414, 584, 756, 925];
for (let index = 0; index < 6; index++) {
  const left = jumpColumns[index];
  const input = await sharp(path.join(root, "UI-images/sprite sheet.png"))
    .extract({ left, top: 545, width: jumpColumns[index + 1] - left, height: 260 })
    .png().toBuffer();
  const sprite = await sharp(input).resize({ height: 286, kernel: "nearest" }).png().toBuffer();
  const spriteWidth = (await sharp(sprite).metadata()).width;
  const x = Math.round(260 - (jumpCenters[index] - left) * 1.1);
  await sharp({ create: { width, height, channels: 4, background: "#00000000" } })
    .composite([{ input: sprite, left: Math.min(x, width - spriteWidth), top: height - 286 }])
    .png().toFile(path.join(output, `celebrate-${index + 1}.png`));
}

const idle = await sharp(path.join(output, "idle.png"))
  .resize({ height: 214, kernel: "nearest" }).png().toBuffer();
const idleWidth = (await sharp(idle).metadata()).width;
await sharp({ create: { width, height, channels: 4, background: "#00000000" } })
  .composite([{ input: idle, left: Math.round(260 - idleWidth / 2), top: height - 214 }])
  .png().toFile(path.join(output, "results-idle.png"));
