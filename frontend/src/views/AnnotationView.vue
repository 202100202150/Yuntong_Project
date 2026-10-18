<template>
  <main class="aw-page">
    <section class="aw-shell">
      <header class="aw-header">
        <div class="aw-brand" aria-label="云瞳">云瞳</div>
        <button class="aw-path" type="button" disabled>
          <span>浏览器模式无法提供绝对路径</span>
        </button>
        <div class="aw-current-media">
          <strong>{{ activeMedia?.name ?? '等待导入媒体' }}</strong>
          <small>{{ media.length }} media</small>
        </div>
        <div class="aw-header-actions">
          <span class="aw-model-badge">ONNX PERSON</span>
        </div>
      </header>

      <aside class="aw-toolbar">
        <button type="button" disabled><Home /></button>
        <button type="button" :class="{ active: tool === 'pan' }" @click="tool = 'pan'"><Hand /></button>
        <button type="button" :class="{ active: tool === 'pointer' }" @click="tool = 'pointer'"><MousePointer2 /></button>
      </aside>

      <section ref="stageRef" class="aw-stage">
        <canvas ref="canvasRef" />
      </section>

      <aside class="aw-inspector">
        <section class="aw-panel">
          <div class="aw-panel-heading">
            <div><FileImage /><strong>Images / Media</strong><span>{{ media.length }}</span></div>
          </div>
          <p class="aw-empty-list">Vue 版标注工作台待实现</p>
        </section>
        <section class="aw-panel">
          <div class="aw-panel-heading"><div><Square /><strong>Objects</strong><span>0</span></div></div>
          <p class="aw-empty-objects">请在 Vue 版中接入 ONNX Runtime Web</p>
        </section>
      </aside>
    </section>
  </main>
</template>

<script setup lang="ts">
import { ref } from 'vue';
import { FileImage, Hand, Home, MousePointer2, Square } from 'lucide-vue-next';
import type { MediaItem } from '@/types/camera-types';

const tool = ref<'pointer' | 'pan'>('pointer');
const stageRef = ref<HTMLElement | null>(null);
const canvasRef = ref<HTMLCanvasElement | null>(null);
const media = ref<MediaItem[]>([]);
const activeMedia = ref<MediaItem | null>(null);
</script>

<style src="@/styles/annotation-workspace.css"></style>