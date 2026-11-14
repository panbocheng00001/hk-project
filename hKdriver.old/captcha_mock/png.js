// png.js —— 极简 PNG 编/解码（只用 zlib，无第三方依赖）
// ============================================================================
// 为什么自己写：
//   Node 侧不能像浏览器那样 canvas.toDataURL，也不能装原生库（服务要能在任何机器
//   上 `node server.js` 直接跑）。所以：
//     decode: 只吃 canvas 产出的 8-bit 非隔行 PNG（color type 0/2/4/6），够用
//     encode: 写 color type 6（RGBA），给复刻服务生成题目用
'use strict';
const zlib = require('zlib');

const SIG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

function crc32(buf) {
  let c, crc = 0xffffffff;
  for (let n = 0; n < buf.length; n++) {
    c = (crc ^ buf[n]) & 0xff;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    crc = (crc >>> 8) ^ c;
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const td = Buffer.concat([Buffer.from(type, 'ascii'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(td), 0);
  return Buffer.concat([len, td, crc]);
}

/** RGBA Buffer (w*h*4) -> PNG Buffer */
function encodeRGBA(rgba, w, h) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0);
  ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8;      // bit depth
  ihdr[9] = 6;      // color type RGBA
  ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
  const stride = w * 4;
  const raw = Buffer.alloc((stride + 1) * h);
  for (let y = 0; y < h; y++) {
    raw[y * (stride + 1)] = 0;                       // filter: none
    rgba.copy(raw, y * (stride + 1) + 1, y * stride, y * stride + stride);
  }
  return Buffer.concat([SIG, chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(raw, { level: 9 })), chunk('IEND', Buffer.alloc(0))]);
}

function paeth(a, b, c) {
  const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
  return pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
}

/** PNG Buffer -> {w, h, channels, data: Uint8Array(w*h*channels)} */
function decode(buf) {
  if (!buf.slice(0, 8).equals(SIG)) throw new Error('not a PNG');
  let off = 8, w = 0, h = 0, depth = 0, ctype = 0, interlace = 0;
  const idat = [];
  while (off < buf.length) {
    const len = buf.readUInt32BE(off);
    const type = buf.slice(off + 4, off + 8).toString('ascii');
    const data = buf.slice(off + 8, off + 8 + len);
    if (type === 'IHDR') {
      w = data.readUInt32BE(0); h = data.readUInt32BE(4);
      depth = data[8]; ctype = data[9]; interlace = data[12];
    } else if (type === 'IDAT') idat.push(data);
    else if (type === 'IEND') break;
    off += 12 + len;
  }
  if (depth !== 8) throw new Error('only 8-bit PNG supported, got ' + depth);
  if (interlace) throw new Error('interlaced PNG unsupported');
  const ch = { 0: 1, 2: 3, 4: 2, 6: 4 }[ctype];
  if (!ch) throw new Error('unsupported color type ' + ctype);
  const raw = zlib.inflateSync(Buffer.concat(idat));
  const stride = w * ch;
  const out = Buffer.alloc(stride * h);
  let p = 0;
  for (let y = 0; y < h; y++) {
    const f = raw[p++];
    const line = raw.slice(p, p + stride); p += stride;
    const cur = out.slice(y * stride, y * stride + stride);
    const prev = y ? out.slice((y - 1) * stride, y * stride) : Buffer.alloc(stride);
    for (let x = 0; x < stride; x++) {
      const a = x >= ch ? cur[x - ch] : 0;
      const b = prev[x];
      const c = x >= ch ? prev[x - ch] : 0;
      let v = line[x];
      if (f === 1) v = (v + a) & 0xff;
      else if (f === 2) v = (v + b) & 0xff;
      else if (f === 3) v = (v + ((a + b) >> 1)) & 0xff;
      else if (f === 4) v = (v + paeth(a, b, c)) & 0xff;
      cur[x] = v;
    }
  }
  return { w, h, channels: ch, data: out };
}

/** 统一成 RGBA */
function toRGBA(img) {
  if (img.channels === 4) return img.data;
  const { w, h, channels, data } = img;
  const out = Buffer.alloc(w * h * 4);
  for (let i = 0, j = 0, k = 0; i < w * h; i++, j += channels, k += 4) {
    if (channels === 3) { out[k] = data[j]; out[k + 1] = data[j + 1]; out[k + 2] = data[j + 2]; out[k + 3] = 255; }
    else if (channels === 1) { out[k] = out[k + 1] = out[k + 2] = data[j]; out[k + 3] = 255; }
    else { out[k] = out[k + 1] = out[k + 2] = data[j]; out[k + 3] = data[j + 1]; }
  }
  return out;
}

module.exports = { encodeRGBA, decode, toRGBA, crc32 };
