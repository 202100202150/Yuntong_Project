<template>
  <svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" :stroke-width="strokeWidth" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <rect x="2" y="1.5" width="20" height="6" rx="1.5"/>
    <rect x="2" y="9" width="20" height="6" rx="1.5"/>
    <rect x="2" y="16.5" width="20" height="6" rx="1.5"/>
    <path d="M5.5 4.5h.01M5.5 12h.01M5.5 19.5h.01M15 4.5h3M15 12h3M15 19.5h3"/>
  </svg>
</template>
<script setup lang="ts">
withDefaults(defineProps<{size?:number;strokeWidth?:number}>(),{size:24,strokeWidth:1.8});
</script>
