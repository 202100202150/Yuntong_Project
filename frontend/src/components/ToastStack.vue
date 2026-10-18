<template>
  <div class="dc-toasts" aria-live="polite">
    <div v-for="item in toasts" :key="item.id" :class="['dc-toast', item.tone]">
      {{ item.message }}
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  toasts: { id: number; message: string; tone: 'info' | 'success' | 'error' }[];
}>();
</script>