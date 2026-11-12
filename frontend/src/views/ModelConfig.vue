<template>
  <div class="page-content">
    <!-- 左右布局的父容器 -->
    <div class="cards-container">

      <!-- 左边：模型基础信息 -->
      <div class="config-card">
        <h3>模型基础信息</h3>
        <div class="info-row">
          <span class="label">当前模型文件：</span>
          <span class="value"><strong>drone.onnx</strong></span>
        </div>
        <div class="info-row">
          <span class="label">推理引擎：</span>
          <span class="value">{{ engineStateText }}</span>
        </div>
        <div class="info-row">
          <span class="label">运行状态：</span>
          <span class="value" :class="{ 'text-success': isRunning, 'text-error': isError }">
            {{ runStateText }}
          </span>
        </div>
      </div>

      <!-- 右边：性能指标 (实时) -->
      <div class="config-card">
        <h3>性能指标 (实时)</h3>
        <div class="info-row">
          <span class="label">推理精度：</span>
          <span class="value">{{ precisionText }}</span>
        </div>
        <div class="info-row">
          <span class="label">检测帧率上限：</span>
          <span class="value">{{ detectionFps }} FPS</span>
        </div>
        <div class="info-row">
          <span class="label">当前单帧推理耗时：</span>
          <span class="value">{{ latencyText }}</span>
        </div>
      </div>

    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue';
import { storeToRefs } from 'pinia';
import { useCameraStore } from '@/stores/camera';

const store = useCameraStore();
const { status } = storeToRefs(store);

// 引擎状态
const engineStateText = computed(() => {
  const state = status.value.engineState;
  if (state === 'ready') return 'TensorRT (CUDA 已加速)';
  if (state === 'loading') return '正在加载模型...';
  if (state === 'error') return '模型加载失败';
  return '等待后端上报 (Unreported)';
});

// 运行状态
const isRunning = computed(() => status.value.detecting);
const isError = computed(() => status.value.serviceState === 'error');
const runStateText = computed(() => {
  if (isError.value) return '服务异常/未连接';
  if (isRunning.value) return '正在推理中';
  return '空闲 / 未启动';
});

// 精度
const precisionText = computed(() => {
  return status.value.precision === 'unreported' ? '未知 (FP16/INT8)' : status.value.precision;
});

// 推理耗时
const latencyText = computed(() => {
  const latency = status.value.cameras.camera1.latency;
  return latency ? `${latency.toFixed(1)} ms` : '暂无数据';
});

// 检测上限 (写死，因为后端的默认配置是 12.5)
const detectionFps = 12.5;

// 👇 新增：专属的 1 秒轮询刷新逻辑
let timer: ReturnType<typeof setInterval> | null = null;

onMounted(() => {
  // 页面加载时立刻刷新一次，避免等待 1 秒
  store.refreshStatus();

  // 启动 1 秒定时器
  timer = setInterval(() => {
    store.refreshStatus();
  }, 1000);
});

onUnmounted(() => {
  // 页面销毁时清理定时器，防止内存泄漏
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
});
</script>

<style scoped>
/* 页面容器加宽以适应左右布局 */
.page-content {
  max-width: 1000px;
  margin: 0 auto;
  padding: 32px 24px;
  height: 100%;
  overflow-y: auto;
  box-sizing: border-box;
}

.header-area {
  margin-bottom: 24px;
}

h2 { margin-top: 0; font-size: 24px; color: var(--text-main); margin-bottom: 8px; }
.subtitle { color: var(--text-sub); margin: 0; font-size: 14px; }

/* 核心：Flex 左右并排 */
.cards-container {
  display: flex;
  gap: 24px;
  align-items: stretch;
  margin-bottom: 24px;
}

.config-card {
  flex: 1;
  min-width: 0;
  background: var(--header-bg);
  padding: 24px;
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.05);
  border: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
}

h3 {
  margin-top: 0;
  border-bottom: 1px solid var(--border-color);
  padding-bottom: 12px;
  margin-bottom: 20px;
  font-size: 16px;
  color: var(--text-main);
}

.info-row {
  display: flex;
  margin-bottom: 16px;
  font-size: 14px;
  line-height: 1.5;
}

.info-row:last-child {
  margin-bottom: 0;
}

.label {
  color: var(--text-sub);
  width: 140px;
  flex-shrink: 0;
}

.value {
  color: var(--text-main);
  font-weight: 500;
}

.text-success { color: #10b981; }
.text-error { color: #ef4444; }

/* 响应式设计：当窗口宽度过窄时，自动变为上下堆叠 */
@media (max-width: 768px) {
  .cards-container {
    flex-direction: column;
  }
}
</style>
