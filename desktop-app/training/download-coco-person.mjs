import { createHash } from 'node:crypto';
import { createReadStream, createWriteStream } from 'node:fs';
import fs from 'node:fs/promises';
import path from 'node:path';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { spawn } from 'node:child_process';

const projectRoot = path.resolve(import.meta.dirname, '..');
const datasetRoot = path.join(projectRoot, 'training', 'datasets', 'coco-person-2017');
const downloadsRoot = path.join(datasetRoot, '_downloads');
const annotationsRoot = path.join(downloadsRoot, 'annotations');
const annotationZip = path.join(downloadsRoot, 'annotations_trainval2017.zip');
const annotationMetadataPath = annotationZip + '.metadata.json';
const cocoBaseUrl =
  'https://s3.us-east-1.amazonaws.com/images.cocodataset.org';
const annotationUrl = cocoBaseUrl + '/annotations/annotations_trainval2017.zip';
const annotationZipSize = 252_907_541;
const annotationExpectedEtag = 'f4bbac642086de4f52a3fdda2de5fa2c';
const rangeConcurrency = 32;
const imageConcurrency = 256;
const rangeChunkSize = 4 * 1024 * 1024;
const requestTimeoutMs = 90_000;

const sleep = (milliseconds) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

async function fileSize(filePath) {
  try {
    return (await fs.stat(filePath)).size;
  } catch {
    return 0;
  }
}

async function readJson(filePath) {
  try {
    return JSON.parse(await fs.readFile(filePath, 'utf8'));
  } catch {
    return null;
  }
}

async function writeJsonAtomic(filePath, value) {
  const temporaryPath = filePath + '.tmp';
  await fs.writeFile(temporaryPath, JSON.stringify(value, null, 2) + '\n', 'utf8');
  await fs.rm(filePath, { force: true });
  await fs.rename(temporaryPath, filePath);
}

async function fileHash(filePath, algorithm) {
  const hash = createHash(algorithm);
  for await (const chunk of createReadStream(filePath)) hash.update(chunk);
  return hash.digest('hex').toUpperCase();
}

const sha256 = (filePath) => fileHash(filePath, 'sha256');
const md5 = (filePath) => fileHash(filePath, 'md5');

function parseIntegerHeader(value, headerName) {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 0)
    throw new Error('Invalid ' + headerName + ' header: ' + value);
  return parsed;
}

function parseContentRange(value) {
  const match = /^bytes (\d+)-(\d+)\/(\d+)$/i.exec(value || '');
  if (!match) throw new Error('Invalid Content-Range header: ' + value);
  return {
    start: Number(match[1]),
    end: Number(match[2]),
    total: Number(match[3]),
  };
}

function normalizeEtag(value) {
  return String(value || '').trim().replace(/^W\//i, '').replace(/^"|"$/g, '').toLowerCase();
}

function fetchWithTimeout(url, options = {}) {
  return fetch(url, {
    ...options,
    redirect: 'follow',
    signal: AbortSignal.timeout(requestTimeoutMs),
  });
}

async function concurrent(items, limit, worker) {
  let cursor = 0;
  const runners = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (true) {
      const index = cursor;
      cursor += 1;
      if (index >= items.length) return;
      await worker(items[index], index);
    }
  });
  await Promise.all(runners);
}

async function getRemoteMetadata(url) {
  const response = await fetchWithTimeout(url, {
    method: 'HEAD',
    headers: { 'Accept-Encoding': 'identity' },
  });
  if (!response.ok)
    throw new Error('Annotation metadata request returned HTTP ' + response.status);
  const contentLength = parseIntegerHeader(
    response.headers.get('content-length'),
    'Content-Length',
  );
  const etag = response.headers.get('etag');
  if (!etag) throw new Error('Official annotation response did not provide an ETag.');
  if (contentLength !== annotationZipSize)
    throw new Error(
      'Official annotation size changed: expected ' +
        annotationZipSize +
        ', received ' +
        contentLength,
    );
  if (normalizeEtag(etag) !== annotationExpectedEtag)
    throw new Error('Official annotation ETag did not match the pinned release.');
  return { sourceUrl: response.url, contentLength, etag };
}

function sameRemoteMetadata(left, right) {
  return Boolean(
    left &&
      left.sourceUrl === right.sourceUrl &&
      left.contentLength === right.contentLength &&
      left.etag === right.etag,
  );
}

async function downloadRangePart(url, remote, part) {
  const expected = part.end - part.start + 1;
  for (let attempt = 1; attempt <= 8; attempt += 1) {
    let completed = await fileSize(part.file);
    if (completed === expected) return;
    if (completed > expected) {
      await fs.rm(part.file, { force: true });
      completed = 0;
    }
    const from = part.start + completed;
    try {
      const response = await fetchWithTimeout(url, {
        headers: {
          Range: 'bytes=' + from + '-' + part.end,
          'If-Range': remote.etag,
          'Accept-Encoding': 'identity',
        },
      });
      if (response.status !== 206 || !response.body)
        throw new Error('Range request returned HTTP ' + response.status);
      const contentRange = parseContentRange(response.headers.get('content-range'));
      const contentLength = parseIntegerHeader(
        response.headers.get('content-length'),
        'Content-Length',
      );
      if (
        contentRange.start !== from ||
        contentRange.end > part.end ||
        contentRange.end < contentRange.start ||
        contentRange.total !== remote.contentLength ||
        contentLength !== contentRange.end - contentRange.start + 1
      )
        throw new Error('Range response metadata did not match the requested bytes.');
      const output = createWriteStream(part.file, { flags: completed ? 'a' : 'w' });
      await pipeline(Readable.fromWeb(response.body), output);
      const finalSize = await fileSize(part.file);
      if (finalSize > expected) {
        await fs.rm(part.file, { force: true });
        throw new Error('Downloaded range exceeded its expected size.');
      }
      if (finalSize !== expected)
        throw new Error(
          'Part size mismatch: expected ' + expected + ', received ' + finalSize,
        );
      return;
    } catch (error) {
      if (attempt === 8) throw error;
      await sleep(Math.min(30_000, attempt * 2_000 + Math.random() * 1_000));
    }
  }
}

async function downloadRanged(url, outputPath, remote) {
  const partsRoot = outputPath + '.parts';
  const manifestPath = path.join(partsRoot, 'manifest.json');
  const expectedManifest = {
    sourceUrl: remote.sourceUrl,
    contentLength: remote.contentLength,
    etag: remote.etag,
    chunkSize: rangeChunkSize,
  };
  const existingManifest = await readJson(manifestPath);
  if (
    !sameRemoteMetadata(existingManifest, expectedManifest) ||
    existingManifest?.chunkSize !== rangeChunkSize
  )
    await fs.rm(partsRoot, { recursive: true, force: true });
  await fs.mkdir(partsRoot, { recursive: true });
  await writeJsonAtomic(manifestPath, expectedManifest);

  const parts = [];
  for (
    let start = 0, index = 0;
    start < remote.contentLength;
    start += rangeChunkSize
  ) {
    const end = Math.min(remote.contentLength - 1, start + rangeChunkSize - 1);
    parts.push({
      start,
      end,
      file: path.join(partsRoot, String(index).padStart(5, '0') + '.part'),
    });
    index += 1;
  }
  let completedParts = 0;
  await concurrent(parts, rangeConcurrency, async (part) => {
    await downloadRangePart(url, remote, part);
    completedParts += 1;
    if (completedParts % 8 === 0 || completedParts === parts.length)
      console.log('Annotations chunks: ' + completedParts + '/' + parts.length);
  });

  const assemblingPath = outputPath + '.assembling';
  await fs.rm(assemblingPath, { force: true });
  await fs.writeFile(assemblingPath, new Uint8Array());
  for (const part of parts) await fs.appendFile(assemblingPath, await fs.readFile(part.file));
  if ((await fileSize(assemblingPath)) !== remote.contentLength)
    throw new Error('Merged annotation ZIP has an unexpected size.');
  await fs.rm(outputPath, { force: true });
  await fs.rename(assemblingPath, outputPath);
  return partsRoot;
}

async function run(command, args) {
  await new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: 'inherit', windowsHide: true });
    child.on('error', reject);
    child.on('exit', (code) => {
      if (code === 0) resolve();
      else reject(new Error(command + ' exited with code ' + code));
    });
  });
}

async function runCapture(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { windowsHide: true });
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk) => {
      stdout += chunk;
    });
    child.stderr.on('data', (chunk) => {
      stderr += chunk;
    });
    child.on('error', reject);
    child.on('exit', (code) => {
      if (code === 0) resolve(stdout);
      else reject(new Error(command + ' exited with code ' + code + ': ' + stderr));
    });
  });
}

async function validateArchiveEntries(archivePath) {
  const listing = await runCapture('tar', ['-tf', archivePath]);
  const entries = listing.split(/\r?\n/).filter(Boolean);
  if (!entries.length) throw new Error('Annotation ZIP contains no files.');
  for (const rawEntry of entries) {
    const entry = rawEntry.replaceAll('\\', '/');
    const segments = entry.split('/').filter(Boolean);
    if (
      entry.startsWith('/') ||
      /^[A-Za-z]:/.test(entry) ||
      segments.some((segment) => segment === '.' || segment === '..') ||
      segments[0] !== 'annotations'
    )
      throw new Error('Unsafe annotation ZIP entry: ' + rawEntry);
  }
}

async function ensureAnnotationArchive() {
  const remote = await getRemoteMetadata(annotationUrl);
  const existingSize = await fileSize(annotationZip);
  if (existingSize === remote.contentLength) {
    const existingMd5 = await md5(annotationZip);
    if (existingMd5.toLowerCase() === annotationExpectedEtag) {
      await validateArchiveEntries(annotationZip);
      const existingSha = await sha256(annotationZip);
      const metadata = { ...remote, md5: existingMd5, sha256: existingSha };
      await writeJsonAtomic(annotationMetadataPath, metadata);
      await fs.rm(annotationZip + '.parts', { recursive: true, force: true });
      return metadata;
    }
  }

  await fs.rm(annotationZip, { force: true });
  await fs.rm(annotationMetadataPath, { force: true });
  const partsRoot = await downloadRanged(annotationUrl, annotationZip, remote);
  try {
    const archiveMd5 = await md5(annotationZip);
    if (archiveMd5.toLowerCase() !== annotationExpectedEtag)
      throw new Error('Annotation ZIP MD5 did not match the pinned official ETag.');
    await validateArchiveEntries(annotationZip);
    const archiveSha = await sha256(annotationZip);
    const metadata = { ...remote, md5: archiveMd5, sha256: archiveSha };
    await writeJsonAtomic(annotationMetadataPath, metadata);
    await fs.rm(partsRoot, { recursive: true, force: true });
    return metadata;
  } catch (error) {
    await fs.rm(annotationZip, { force: true });
    await fs.rm(annotationMetadataPath, { force: true });
    await fs.rm(partsRoot, { recursive: true, force: true });
    throw error;
  }
}

function safeMediaFileName(fileName) {
  if (
    typeof fileName !== 'string' ||
    path.basename(fileName) !== fileName ||
    !/^\d{12}\.jpg$/i.test(fileName)
  )
    throw new Error('Unsafe COCO image file name: ' + String(fileName));
  return fileName;
}

function safeJoin(root, fileName) {
  const target = path.resolve(root, fileName);
  const relative = path.relative(root, target);
  if (
    !relative ||
    relative === '..' ||
    relative.startsWith('..' + path.sep) ||
    path.isAbsolute(relative)
  )
    throw new Error('Resolved path escaped its dataset directory: ' + fileName);
  return target;
}

async function downloadImage(url, target) {
  if ((await fileSize(target)) > 0) return;
  const partial = target + '.part';
  for (let attempt = 1; attempt <= 8; attempt += 1) {
    try {
      let partialSize = await fileSize(partial);
      const response = await fetchWithTimeout(url, {
        headers: {
          ...(partialSize ? { Range: 'bytes=' + partialSize + '-' } : {}),
          'Accept-Encoding': 'identity',
        },
      });
      if (response.status === 416 && partialSize) {
        await fs.rm(partial, { force: true });
        if (attempt === 8)
          throw new Error('Server repeatedly rejected the image resume range for ' + url);
        await sleep(500 + Math.random() * 500);
        continue;
      }
      if (!response.body || ![200, 206].includes(response.status))
        throw new Error('Image request returned HTTP ' + response.status);
      const contentType = response.headers.get('content-type') || '';
      if (!contentType.toLowerCase().startsWith('image/jpeg'))
        throw new Error('Unexpected image Content-Type: ' + contentType);
      const responseEtag = normalizeEtag(response.headers.get('etag'));
      if (!/^[a-f0-9]{32}$/.test(responseEtag))
        throw new Error('Image response did not provide a simple MD5 ETag.');

      let expectedTotal;
      let append = false;
      if (response.status === 206) {
        const contentRange = parseContentRange(response.headers.get('content-range'));
        const contentLength = parseIntegerHeader(
          response.headers.get('content-length'),
          'Content-Length',
        );
        if (
          contentRange.start !== partialSize ||
          contentRange.end < contentRange.start ||
          contentLength !== contentRange.end - contentRange.start + 1
        )
          throw new Error('Image range response did not match the requested bytes.');
        expectedTotal = contentRange.total;
        append = true;
      } else {
        expectedTotal = parseIntegerHeader(
          response.headers.get('content-length'),
          'Content-Length',
        );
        if (partialSize) {
          await fs.rm(partial, { force: true });
          partialSize = 0;
        }
      }

      const output = createWriteStream(partial, { flags: append ? 'a' : 'w' });
      await pipeline(Readable.fromWeb(response.body), output);
      const downloadedSize = await fileSize(partial);
      if (downloadedSize > expectedTotal) {
        await fs.rm(partial, { force: true });
        throw new Error('Downloaded image exceeded its expected size.');
      }
      if (downloadedSize !== expectedTotal)
        throw new Error(
          'Image size mismatch: expected ' + expectedTotal + ', received ' + downloadedSize,
        );
      const downloadedMd5 = (await md5(partial)).toLowerCase();
      if (downloadedMd5 !== responseEtag) {
        await fs.rm(partial, { force: true });
        throw new Error('Downloaded image MD5 did not match the official S3 ETag.');
      }
      await fs.rename(partial, target);
      return;
    } catch (error) {
      if (attempt === 8) throw error;
      await sleep(Math.min(20_000, attempt * 1_500 + Math.random() * 750));
    }
  }
}

function yoloLines(image, annotations) {
  if (
    !Number.isFinite(image.width) ||
    !Number.isFinite(image.height) ||
    image.width <= 0 ||
    image.height <= 0
  )
    throw new Error('Invalid COCO image dimensions for image ' + image.id);
  const lines = [];
  for (const annotation of annotations) {
    if (
      annotation.bbox.length < 4 ||
      !annotation.bbox.slice(0, 4).every(Number.isFinite)
    )
      continue;
    const [rawX, rawY, rawWidth, rawHeight] = annotation.bbox;
    const x1 = Math.max(0, rawX);
    const y1 = Math.max(0, rawY);
    const x2 = Math.min(image.width, rawX + rawWidth);
    const y2 = Math.min(image.height, rawY + rawHeight);
    const width = x2 - x1;
    const height = y2 - y1;
    if (width < 1 || height < 1) continue;
    const centerX = (x1 + x2) / 2 / image.width;
    const centerY = (y1 + y2) / 2 / image.height;
    lines.push(
      [
        '0',
        centerX.toFixed(6),
        centerY.toFixed(6),
        (width / image.width).toFixed(6),
        (height / image.height).toFixed(6),
      ].join(' '),
    );
  }
  return lines;
}

async function prepareSplit(split) {
  if (!['train', 'val'].includes(split)) throw new Error('Unsupported split: ' + split);
  const jsonPath = path.join(
    annotationsRoot,
    'annotations',
    'instances_' + split + '2017.json',
  );
  console.log('Reading ' + jsonPath);
  const data = JSON.parse(await fs.readFile(jsonPath, 'utf8'));
  const imagesById = new Map(data.images.map((image) => [image.id, image]));
  const annotationsByImage = new Map();
  for (const annotation of data.annotations) {
    if (
      annotation.category_id !== 1 ||
      annotation.iscrowd === 1 ||
      !Array.isArray(annotation.bbox)
    )
      continue;
    const list = annotationsByImage.get(annotation.image_id) || [];
    list.push(annotation);
    annotationsByImage.set(annotation.image_id, list);
  }

  const records = [];
  for (const [imageId, annotations] of annotationsByImage) {
    const image = imagesById.get(imageId);
    if (!image) continue;
    const fileName = safeMediaFileName(image.file_name);
    const labels = yoloLines(image, annotations);
    if (labels.length) records.push({ image: { ...image, file_name: fileName }, labels });
  }
  records.sort((left, right) => left.image.id - right.image.id);

  const imageDirectory = path.join(datasetRoot, 'images', split);
  const labelDirectory = path.join(datasetRoot, 'labels', split);
  await fs.mkdir(imageDirectory, { recursive: true });
  await fs.mkdir(labelDirectory, { recursive: true });

  let completed = 0;
  await concurrent(records, imageConcurrency, async ({ image, labels }) => {
    const imageTarget = safeJoin(imageDirectory, image.file_name);
    const imageUrl =
      cocoBaseUrl + '/' + split + '2017/' + encodeURIComponent(image.file_name);
    await downloadImage(imageUrl, imageTarget);
    const labelName = image.file_name.replace(/\.jpg$/i, '.txt');
    await fs.writeFile(
      safeJoin(labelDirectory, labelName),
      labels.join('\n') + '\n',
      'utf8',
    );
    completed += 1;
    if (completed % 500 === 0 || completed === records.length)
      console.log(split + ': ' + completed + '/' + records.length);
  });
  return { split, records, imageDirectory, labelDirectory };
}

async function auditSplit(prepared) {
  const expectedImages = new Set(prepared.records.map(({ image }) => image.file_name));
  const expectedLabels = new Set(
    prepared.records.map(({ image }) => image.file_name.replace(/\.jpg$/i, '.txt')),
  );
  const imageEntries = await fs.readdir(prepared.imageDirectory, { withFileTypes: true });
  const labelEntries = await fs.readdir(prepared.labelDirectory, { withFileTypes: true });
  const actualImages = imageEntries.filter((entry) => entry.isFile() && /\.jpg$/i.test(entry.name));
  const actualLabels = labelEntries.filter((entry) => entry.isFile() && /\.txt$/i.test(entry.name));
  if (
    actualImages.length !== expectedImages.size ||
    actualImages.some((entry) => !expectedImages.has(entry.name))
  )
    throw new Error(prepared.split + ' image count or file set does not match annotations.');
  if (
    actualLabels.length !== expectedLabels.size ||
    actualLabels.some((entry) => !expectedLabels.has(entry.name))
  )
    throw new Error(prepared.split + ' label count or file set does not match annotations.');

  let boxes = 0;
  for (const entry of actualLabels) {
    const content = await fs.readFile(path.join(prepared.labelDirectory, entry.name), 'utf8');
    const lines = content.split(/\r?\n/).filter(Boolean);
    if (!lines.length) throw new Error('Empty YOLO label file: ' + entry.name);
    for (const line of lines) {
      const fields = line.trim().split(/\s+/);
      if (fields.length !== 5 || fields[0] !== '0')
        throw new Error('Invalid YOLO label line in ' + entry.name + ': ' + line);
      const coordinates = fields.slice(1).map(Number);
      if (
        coordinates.some((value) => !Number.isFinite(value) || value < 0 || value > 1) ||
        coordinates[2] <= 0 ||
        coordinates[3] <= 0
      )
        throw new Error('Out-of-range YOLO coordinates in ' + entry.name + ': ' + line);
      boxes += 1;
    }
  }
  return { images: actualImages.length, labels: actualLabels.length, boxes };
}

async function findIncompleteFiles(root) {
  const found = [];
  async function walk(directory) {
    let entries;
    try {
      entries = await fs.readdir(directory, { withFileTypes: true });
    } catch (error) {
      if (error.code === 'ENOENT') return;
      throw error;
    }
    for (const entry of entries) {
      const entryPath = path.join(directory, entry.name);
      if (entry.isDirectory()) await walk(entryPath);
      else if (entry.name.endsWith('.part') || entry.name.endsWith('.assembling'))
        found.push(entryPath);
    }
  }
  await walk(root);
  return found;
}

async function ensureAnnotationsExtracted(archiveSha) {
  const trainJson = path.join(annotationsRoot, 'annotations', 'instances_train2017.json');
  const valJson = path.join(annotationsRoot, 'annotations', 'instances_val2017.json');
  const markerPath = path.join(annotationsRoot, '.source-sha256');
  let marker = '';
  try {
    marker = (await fs.readFile(markerPath, 'utf8')).trim();
  } catch {
    // Missing marker requires a clean extraction.
  }
  if (
    marker === archiveSha &&
    (await fileSize(trainJson)) > 0 &&
    (await fileSize(valJson)) > 0
  )
    return;
  await fs.rm(annotationsRoot, { recursive: true, force: true });
  await fs.mkdir(annotationsRoot, { recursive: true });
  await run('tar', ['-xf', annotationZip, '-C', annotationsRoot]);
  if ((await fileSize(trainJson)) <= 0 || (await fileSize(valJson)) <= 0)
    throw new Error('Required COCO instance annotation files were not extracted.');
  await fs.writeFile(markerPath, archiveSha + '\n', 'utf8');
}

async function main() {
  await fs.mkdir(downloadsRoot, { recursive: true });
  console.log('Downloading official COCO 2017 annotations over HTTPS...');
  const annotationMetadata = await ensureAnnotationArchive();
  console.log('Annotation SHA-256: ' + annotationMetadata.sha256);
  await ensureAnnotationsExtracted(annotationMetadata.sha256);

  const preparedTrain = await prepareSplit('train');
  const preparedVal = await prepareSplit('val');
  const train = await auditSplit(preparedTrain);
  const val = await auditSplit(preparedVal);
  const incompleteFiles = await findIncompleteFiles(datasetRoot);
  if (incompleteFiles.length)
    throw new Error('Incomplete download files remain: ' + incompleteFiles.join(', '));

  const yaml = [
    'path: ' + JSON.stringify(datasetRoot.replaceAll('\\', '/')),
    'train: images/train',
    'val: images/val',
    '',
    'names:',
    '  0: person',
    '',
  ].join('\n');
  await fs.writeFile(path.join(datasetRoot, 'data.yaml'), yaml, 'utf8');
  await writeJsonAtomic(path.join(datasetRoot, 'download-summary.json'), {
    source: 'COCO 2017 official server',
    sourceUrl: annotationMetadata.sourceUrl,
    sourceEtag: annotationMetadata.etag,
    downloadedAt: new Date().toISOString(),
    category: 'person',
    categoryId: 1,
    ignoredCrowdAnnotations: true,
    train,
    val,
    annotationZipBytes: await fileSize(annotationZip),
    annotationZipMd5: annotationMetadata.md5,
    annotationZipSha256: annotationMetadata.sha256,
    validation: {
      archiveEntriesSafe: true,
      actualFileCountsMatch: true,
      noPartialFiles: true,
      yoloCoordinatesWithinZeroAndOne: true,
    },
  });
  console.log('COCO Person dataset is ready at ' + datasetRoot);
}

await main();
