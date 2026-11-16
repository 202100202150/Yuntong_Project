<template>
  <div class="page-content">
    <h2>设备与定位配置</h2>
    <p class="subtitle">统一使用米。外参解算以两光学中心中点为原点，X 向左、Y 向上、Z 向前。</p>
    <div class="cards-container camera-summary">
      <div v-for="id in cameraIds" :key="id" class="config-card">
        <h3>{{ id === 'camera1' ? 'Camera 1 / C1' : 'Camera 2 / C2' }}</h3>
        <div class="form-group"><label>视频通道</label><input :value="id === 'camera1' ? 'C1' : 'C2'" disabled /></div>
        <div class="form-row">
          <div class="form-group"><label>当前解码分辨率</label><input :value="cameraResolution(id)" disabled /></div>
          <div class="form-group"><label>当前检测帧率</label><input :value="store.status.cameras[id].detectionFps.toFixed(1) + ' FPS'" disabled /></div>
        </div>
      </div>
    </div>

    <p v-if="error" class="feedback error" role="alert">{{ error }}</p>
    <p v-if="success" class="feedback success" role="status">{{ success }}</p>
    <p v-if="loading">正在读取定位配置…</p>
    <form v-if="config" @submit.prevent="save">
      <div class="calibration-notice">
        <strong>{{ isCalibrated ? '已填写标定内参' : '尚未完成相机标定' }}</strong>
        <p>位置、间距和角度用于双目三角测量。完成两路相机标定后，空间视图会显示米制无人机位置。</p>
        <p>相机移位后，在下方按 C1、C2 导入同一 AprilGrid 世界坐标系的米制外参。导入两份后，系统自动计算基线、左右位置和原点距离。</p>
      </div>
      <fieldset v-if="!pendingExtrinsic" :disabled="busy || jsonDirty" class="geometry-fields">
        <div class="config-card">
          <h3>双目安装位置</h3>
          <p class="help">{{ derivedPositions ? '中点和基线根据两份外参自动计算。坐标轴的上方和前方以双目相机安装方向为参考。' : '导入两份外参后，可根据实测光学中心自动更新此处。' }}</p>
          <div class="form-row">
            <div v-for="axis in axes" :key="axis" class="form-group"><label :for="'center-' + axis">中点 {{ axis.toUpperCase() }} / m</label><input :id="'center-' + axis" v-model.number="config.rig.midpoint[axis]" type="number" step="any" :readonly="!!derivedPositions" required /></div>
          </div>
          <div class="form-row">
            <div class="form-group"><label for="baseline">基线长度 / m</label><input id="baseline" v-model.number="config.rig.baselineMeters" type="number" min="0.001" step="any" :readonly="!!derivedPositions" required /></div>
            <div v-if="!derivedPositions" class="form-group"><label for="baseline-yaw">基线水平旋转 / °</label><input id="baseline-yaw" v-model.number="config.rig.baselineYawDeg" type="number" min="-180" max="180" step="0.1" required /></div>
          </div>
          <p v-if="!derivedPositions" class="help">基线角 0° 表示 C1→C2 沿 +X，正值转向 +Z。镜头水平朝向采用不同的零方向。</p>
        </div>
        <div class="cards-container">
          <div v-for="id in cameraIds" :key="id" class="config-card">
            <h3>{{ cameraLabel(id, derivedPositions?.[id]) }}{{ derivedPositions ? ' 光学中心' : ' 位置与朝向' }}</h3>
            <template v-if="derivedPositions">
              <p class="help">按外参解算的光学中心位置。距原点 {{ originDistance(derivedPositions[id]) }} m。</p>
              <div class="form-row">
                <div v-for="axis in axes" :key="axis" class="form-group"><label :for="id + '-position-' + axis">{{ axis.toUpperCase() }} / m</label><input :id="id + '-position-' + axis" :value="derivedPositions[id][axis]" readonly /></div>
              </div>
            </template>
            <template v-else>
              <p class="help">手动安装参数用于兼容模式。启用外参时，朝向由 extrinsics 字段计算。</p>
              <div class="form-row">
                <div v-for="axis in axes" :key="axis" class="form-group"><label :for="id + '-offset-' + axis">{{ axis.toUpperCase() }} 偏移 / m</label><input :id="id + '-offset-' + axis" v-model.number="config.cameras[id].offset[axis]" type="number" step="0.001" required /></div>
              </div>
              <div class="form-row">
                <div class="form-group"><label :for="id + '-yaw'">水平朝向 / °</label><input :id="id + '-yaw'" v-model.number="config.cameras[id].yawDeg" type="number" min="-180" max="180" step="0.1" required /></div>
                <div class="form-group"><label :for="id + '-pitch'">俯仰角 / °</label><input :id="id + '-pitch'" v-model.number="config.cameras[id].pitchDeg" type="number" min="-90" max="90" step="0.1" required /></div>
                <div class="form-group"><label :for="id + '-roll'">翻滚角 / °</label><input :id="id + '-roll'" v-model.number="config.cameras[id].rollDeg" type="number" min="-180" max="180" step="0.1" required /></div>
              </div>
            </template>
            <div class="intrinsics-summary"><strong>镜头标定</strong><span>来源：{{ config.cameras[id].intrinsics.source }}</span><span>尺寸：{{ config.cameras[id].intrinsics.imageWidth }} × {{ config.cameras[id].intrinsics.imageHeight }}</span><span>fx / fy：{{ config.cameras[id].intrinsics.fx ?? '未标定' }} / {{ config.cameras[id].intrinsics.fy ?? '未标定' }} px</span></div>
          </div>
        </div>
        <details class="config-card advanced-quality">
          <summary>定位质量阈值</summary>
          <p class="help">无人机定位使用修正后的接收时间估计双路帧差，实际阈值最多为 40 ms，可填写更严格的上限。</p>
          <div class="form-row">
            <div class="form-group"><label for="max-skew">最大双路帧时间差 / ms</label><input id="max-skew" v-model.number="config.quality.maxFrameSkewMs" type="number" min="0" step="1" required /></div>
            <div class="form-group"><label for="max-reprojection">最大重投影误差 / px</label><input id="max-reprojection" v-model.number="config.quality.maxReprojectionErrorPx" type="number" min="0.1" step="0.1" required /></div>
            <div class="form-group"><label for="max-range">最大定位距离 / m</label><input id="max-range" v-model.number="config.quality.maxRangeMeters" type="number" min="0.1" step="0.1" required /></div>
          </div>
        </details>
      </fieldset>
      <details class="config-card calibration-json">
        <summary>导入标定文件 / 编辑完整配置</summary>
        <p class="help">可导入完整定位配置，也可分别导入标定文件。两份外参应按相机编号导入，使用相同且固定的 AprilGrid 板姿态与坐标轴。待两份外参均导入后，核对计算结果并保存。</p>
        <label class="file-label" for="calibration-file">标定配置 JSON</label>
        <input id="calibration-file" type="file" accept=".json,application/json" :disabled="busy || !!pendingExtrinsic || jsonDirty" @change="importConfig" />
        <div class="calibration-file-grid">
          <label class="file-label">C1 内参 JSON<input type="file" accept=".json,application/json" :disabled="busy || !!pendingExtrinsic || jsonDirty" @change="event => importCalibrationFile(event, 'camera1', 'intrinsic')" /></label>
          <label class="file-label">C2 内参 JSON<input type="file" accept=".json,application/json" :disabled="busy || !!pendingExtrinsic || jsonDirty" @change="event => importCalibrationFile(event, 'camera2', 'intrinsic')" /></label>
          <label class="file-label">C1 外参 JSON<input type="file" accept=".json,application/json" :disabled="busy || jsonDirty" @change="event => importCalibrationFile(event, 'camera1', 'extrinsic')" /></label>
          <label class="file-label">C2 外参 JSON<input type="file" accept=".json,application/json" :disabled="busy || jsonDirty" @change="event => importCalibrationFile(event, 'camera2', 'extrinsic')" /></label>
        </div>
        <p v-if="pendingExtrinsic" class="help" role="status">{{ cameraLabel(pendingExtrinsic) }} 外参已暂存。请导入 {{ cameraLabel(pendingExtrinsic === 'camera1' ? 'camera2' : 'camera1') }} 的新外参，或点击重新读取以取消本次导入。</p>
        <label class="file-label" for="config-json">完整配置内容</label>
        <textarea id="config-json" v-model="configJson" :disabled="busy" :readonly="!!pendingExtrinsic" spellcheck="false" @input="jsonDirty = true" />
        <div class="json-actions"><button type="button" :disabled="!jsonDirty || busy" @click="applyJson">将 JSON 应用到表单</button><button type="button" :disabled="!jsonDirty || busy" @click="discardJson">取消 JSON 编辑</button></div>
        <p v-if="jsonDirty" class="help">JSON 尚未应用，请先应用或取消，之后再保存配置。</p>
      </details>
      <div class="footer-area"><button type="submit" class="save-btn" :disabled="busy || jsonDirty || !!pendingExtrinsic">{{ saving ? '正在保存…' : '保存定位配置' }}</button><button type="button" :disabled="busy" @click="load">重新读取</button></div>
      <p class="help">保存后空间视图会更新相机位置，下一组有效双路检测帧使用新参数。</p>
    </form>
    <button v-else-if="!loading" type="button" @click="load">重新读取配置</button>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { cameraBridge } from '@/api/camera-bridge';
import { cameraLabel, originDistance } from '@/lib/camera-position';
import { useCameraStore } from '@/stores/camera';
import type { CameraId, SpatialConfig } from '@/types/camera-types';

const store = useCameraStore();
const cameraIds: CameraId[] = ['camera1', 'camera2'];
const axes = ['x', 'y', 'z'] as const;
const config = ref<SpatialConfig | null>(null);
const configJson = ref(''); const jsonDirty = ref(false);
const loading = ref(false); const saving = ref(false); const error = ref(''); const success = ref('');
const importing = ref(false);
const pendingExtrinsic = ref<CameraId | null>(null);
const busy = computed(() => loading.value || saving.value || importing.value);
const derivedPositions = computed(() => config.value?.rig.geometrySource === 'extrinsics' ? config.value.rig.cameraPositions : null);
const isNumber = (item: unknown) => typeof item === 'number' && Number.isFinite(item);
const isCalibrated = computed(() => !!config.value && cameraIds.every(id => {
  const intrinsics = config.value!.cameras[id].intrinsics;
  return String(intrinsics.source).startsWith('calibrated') && Number(intrinsics.fx) > 0 && Number(intrinsics.fy) > 0;
}));
const cameraResolution = (id: CameraId) => {
  const camera = store.status.cameras[id];
  return camera.width && camera.height ? `${camera.width} × ${camera.height}` : '等待视频流';
};
watch(config, value => { if (!jsonDirty.value) configJson.value = JSON.stringify(value, null, 2); }, { deep: true });

function readConfigJson(text: string): SpatialConfig {
  const parsed = JSON.parse(text);
  const value = parsed.config ?? parsed;
  if (value?.version !== 1 || value?.units !== 'm' || !value?.rig?.midpoint || !value?.cameras?.camera1?.intrinsics || !value?.cameras?.camera2?.intrinsics || !value?.quality) {
    throw new Error('文件需要完整的 version=1、units=m 定位配置，包含 rig、cameras 和 quality。');
  }
  if (!axes.every(axis => isNumber(value.rig.midpoint[axis])) || !isNumber(value.rig.baselineMeters) || !isNumber(value.rig.baselineYawDeg)) {
    throw new Error('中点坐标、基线和旋转角必须是有效数字。');
  }
  for (const id of cameraIds) {
    const camera = value.cameras[id]; const intrinsics = camera.intrinsics;
    if (!camera.offset || !axes.every(axis => isNumber(camera.offset[axis])) || !['yawDeg', 'pitchDeg', 'rollDeg'].every(key => isNumber(camera[key]))
      || !['imageWidth', 'imageHeight', 'cx', 'cy'].every(key => isNumber(intrinsics[key]))
      || !['fx', 'fy'].every(key => intrinsics[key] === null || isNumber(intrinsics[key]))
      || !Array.isArray(intrinsics.distortion) || !intrinsics.distortion.every(isNumber) || typeof intrinsics.source !== 'string') {
      throw new Error(`${id} 缺少完整的位置、姿态或镜头内参字段。`);
    }
  }
  if (value.extrinsics?.enabled) {
    for (const id of cameraIds) {
      const item = value.extrinsics[id];
      if (!item || !Array.isArray(item.rotationCameraToBoard) || item.rotationCameraToBoard.length !== 3
        || item.rotationCameraToBoard.some((row: unknown) => !Array.isArray(row) || row.length !== 3 || !(row as unknown[]).every(isNumber))
        || !Array.isArray(item.translationCameraInBoard) || item.translationCameraInBoard.length !== 3
        || !item.translationCameraInBoard.every(isNumber)) {
        throw new Error(`${id} 的共同坐标系外参必须包含 3×3 rotationCameraToBoard 和 3 项 translationCameraInBoard。`);
      }
    }
  }
  if (!value.stereo || !['processingScale', 'minDisparity', 'numDisparities', 'blockSize', 'minValidDepthPoints', 'minDepthMeters', 'maxDepthMeters'].every(key => isNumber(value.stereo[key]))) {
    throw new Error('stereo 深度参数字段不完整。');
  }
  if (!['maxFrameSkewMs', 'maxReprojectionErrorPx', 'minFeatureMatches', 'maxRangeMeters'].every(key => isNumber(value.quality[key]))) {
    throw new Error('定位质量阈值字段不完整。');
  }
  return value as SpatialConfig;
}
function replaceConfig(value: SpatialConfig) {
  jsonDirty.value = false; config.value = value; configJson.value = JSON.stringify(value, null, 2);
}
async function load() {
  loading.value = true; error.value = ''; success.value = '';
  try {
    const result = await cameraBridge.getSpatialConfig();
    pendingExtrinsic.value = null;
    replaceConfig(result.config);
    await store.refreshStatus();
  }
  catch (err) { error.value = (err as Error).message; }
  finally { loading.value = false; }
}
async function save() {
  if (!config.value || busy.value || jsonDirty.value || pendingExtrinsic.value) return;
  saving.value = true; error.value = ''; success.value = '';
  try {
    const result = await cameraBridge.saveSpatialConfig(config.value); replaceConfig(result.config);
    await store.refreshStatus(); const application = result as typeof result & { applied?: boolean; message?: string }; success.value = application.applied === false ? (application.message || '定位配置已保存，摄像机服务启动后生效。') : '定位配置已保存；新参数用于下一组有效检测帧。';
  } catch (err) { error.value = (err as Error).message; }
  finally { saving.value = false; }
}
async function applyJson() {
  importing.value = true;
  error.value = ''; success.value = '';
  try {
    await loadConfigJson(configJson.value);
    success.value = 'JSON 已载入表单，保存后生效。';
  }
  catch (err) { error.value = '配置 JSON 无效：' + (err as Error).message; }
  finally { importing.value = false; }
}
function discardJson() { jsonDirty.value = false; configJson.value = JSON.stringify(config.value, null, 2); }
async function loadConfigJson(text: string) {
  const next = readConfigJson(text);
  if (next.rig.geometrySource === 'extrinsics' && next.extrinsics?.enabled) {
    const result = await cameraBridge.deriveSpatialConfig(next);
    replaceConfig(result.config);
  } else {
    replaceConfig(next);
  }
}
async function importConfig(event: Event) {
  const input = event.target as HTMLInputElement; const file = input.files?.[0]; if (!file) return;
  importing.value = true;
  error.value = ''; success.value = '';
  try {
    await loadConfigJson(await file.text());
    success.value = '标定配置已载入，请检查并保存。';
  }
  catch (err) { error.value = '无法导入配置：' + (err as Error).message; }
  finally { importing.value = false; input.value = ''; }
}
async function importCalibrationFile(event: Event, id: CameraId, kind: 'intrinsic' | 'extrinsic') {
  const input = event.target as HTMLInputElement; const file = input.files?.[0]; if (!file || !config.value) return;
  importing.value = true;
  error.value = ''; success.value = '';
  try {
    const parsed = JSON.parse(await file.text());
    const next = JSON.parse(JSON.stringify(config.value)) as SpatialConfig;
    if (kind === 'intrinsic') {
      const K = parsed.K; const D = parsed.D;
      if (!Array.isArray(K) || K.length !== 3 || !Array.isArray(D) || !D.every((value: unknown) => typeof value === 'number' && Number.isFinite(value))) {
        throw new Error('内参文件必须包含 K 和 D。');
      }
      const intrinsic = next.cameras[id].intrinsics;
      intrinsic.imageWidth = Number(parsed.image_width ?? parsed.image_size?.[0] ?? intrinsic.imageWidth);
      intrinsic.imageHeight = Number(parsed.image_height ?? parsed.image_size?.[1] ?? intrinsic.imageHeight);
      intrinsic.fx = Number(K[0][0]); intrinsic.fy = Number(K[1][1]);
      intrinsic.cx = Number(K[0][2]); intrinsic.cy = Number(K[1][2]);
      intrinsic.distortion = D.map(Number); intrinsic.source = `calibrated:${file.name}`;
      replaceConfig(next);
      success.value = `${cameraLabel(id)} 内参已载入，请保存定位配置后生效。`;
    } else {
      await importExtrinsic(next, parsed, id, file.name);
    }
  } catch (err) { error.value = `无法导入${kind === 'intrinsic' ? '内参' : '外参'}：` + (err as Error).message; }
  finally { importing.value = false; input.value = ''; }
}
async function importExtrinsic(next: SpatialConfig, parsed: Record<string, unknown>, id: CameraId, filename: string) {
  if (parsed.coordinate_unit !== undefined && parsed.coordinate_unit !== 'm') {
    throw new Error('外参坐标单位必须为米（coordinate_unit=m）。请用实测板尺寸重新导出。');
  }
  const pose = parsed.camera_to_board as Record<string, unknown> | undefined;
  const R = pose?.rotation_matrix ?? parsed.R_camera_in_board;
  const t = pose?.camera_position_in_board ?? pose?.translation ?? parsed.t_camera_in_board;
  if (!Array.isArray(R) || R.length !== 3 || !R.every(row => Array.isArray(row) && row.length === 3 && row.every(isNumber))
    || !Array.isArray(t) || t.length !== 3 || !t.every(isNumber)) {
    throw new Error('外参文件必须包含相机到标定板的 3×3 旋转矩阵和三维光学中心坐标。');
  }
  const extrinsics = next.extrinsics ?? {
    enabled: false, commonFrame: 'AprilGrid',
    camera1: { rotationCameraToBoard: null, translationCameraInBoard: null },
    camera2: { rotationCameraToBoard: null, translationCameraInBoard: null },
  };
  const otherId = id === 'camera1' ? 'camera2' : 'camera1';
  extrinsics.commonFrame = 'AprilGrid';
  extrinsics[id] = { rotationCameraToBoard: R, translationCameraInBoard: t, source: filename };
  next.extrinsics = extrinsics;

  if (pendingExtrinsic.value !== otherId) {
    extrinsics.enabled = false;
    extrinsics[otherId] = { rotationCameraToBoard: null, translationCameraInBoard: null };
    next.rig.geometrySource = 'manual';
    next.rig.cameraPositions = null;
    pendingExtrinsic.value = id;
    replaceConfig(next);
    return;
  }

  extrinsics.enabled = true;
  const result = await cameraBridge.deriveSpatialConfig(next);
  pendingExtrinsic.value = null;
  replaceConfig(result.config);
  success.value = '两份外参已导入，基线和两相机位置已写入完整配置。请保存定位配置以更新空间视图。';
}
onMounted(load);
</script>

<style scoped>
.page-content { max-width: 1120px; margin: 0 auto; padding: 28px 24px; height: 100%; overflow-y: auto; box-sizing: border-box; }
h2 { margin: 0 0 8px; font-size: 23px; color: var(--text-main); }
.subtitle { color: var(--text-sub); margin: 0 0 24px; font-size: 14px; }
.cards-container { display: flex; gap: 20px; align-items: stretch; margin-bottom: 20px; }
.config-card { flex: 1; min-width: 0; background: var(--header-bg); padding: 22px; border-radius: 12px; border: 1px solid var(--border-color); margin-bottom: 20px; }
.cards-container .config-card { margin-bottom: 0; }
h3 { margin: 0 0 16px; border-bottom: 1px solid var(--border-color); padding-bottom: 12px; font-size: 16px; color: var(--text-main); }
.form-group { display: flex; flex-direction: column; gap: 8px; margin-bottom: 16px; }
.form-row { display: flex; gap: 14px; }
.form-row .form-group { flex: 1; min-width: 0; }
label { font-size: 12px; font-weight: 500; color: var(--text-sub); }
input:not([type=file]), textarea { padding: 10px; border: 1px solid var(--border-color); border-radius: 6px; font-size: 14px; background: var(--header-bg); color: var(--text-main); width: 100%; box-sizing: border-box; }
input:disabled { opacity: .75; background: var(--bg-gradient); }
.geometry-fields { border: 0; padding: 0; margin: 0; min-width: 0; }
.geometry-fields:disabled { opacity: .65; }
.help { color: var(--text-sub); font-size: 12px; line-height: 1.7; margin: 0 0 14px; }
.calibration-notice { border: 1px solid #d7ba67; border-radius: 10px; padding: 16px 20px; margin-bottom: 20px; background: #fffaf0; color: #76591b; font-size: 13px; line-height: 1.7; }
.calibration-notice p { margin: 7px 0 0; }
.intrinsics-summary { display: flex; flex-wrap: wrap; gap: 8px 16px; font-size: 12px; color: var(--text-sub); padding-top: 12px; border-top: 1px solid var(--border-color); }
.intrinsics-summary strong { color: var(--text-main); }
summary { cursor: pointer; font-size: 14px; font-weight: 600; color: var(--text-main); }
details[open] summary { margin-bottom: 18px; }
.file-label { display: block; margin: 16px 0 8px; }
input[type=file] { max-width: 100%; color: var(--text-sub); }
textarea { min-height: 340px; resize: vertical; font: 12px/1.6 Consolas, monospace; }
.calibration-file-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }
.footer-area, .json-actions { display: flex; justify-content: center; gap: 12px; margin: 16px 0; }
button { background: var(--header-bg); color: var(--text-main); border: 1px solid var(--border-color); padding: 10px 16px; border-radius: 7px; font-size: 13px; cursor: pointer; }
.save-btn { background: #2563eb; color: white; border-color: #2563eb; padding: 11px 28px; font-weight: 600; }
button:disabled { opacity: .5; cursor: not-allowed; }
.feedback { padding: 12px 16px; border-radius: 8px; font-size: 13px; overflow-wrap: anywhere; }
.feedback.error { background: #fff1f2; color: #a52e3c; }
.feedback.success { background: #ecfdf5; color: #116b4d; }
@media (max-width: 820px) { .cards-container { flex-direction: column; } .advanced-quality .form-row { flex-wrap: wrap; } .advanced-quality .form-group { flex-basis: 40%; } }
</style>
