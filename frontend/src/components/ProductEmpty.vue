<template><div class="product-empty"><component :is="icon==='video'?VideoOff:icon==='search'?Search:Inbox" :size="34" :stroke-width="1.4"/><strong>{{title}}</strong><p v-if="description">{{description}}</p><slot/></div></template>
<script setup lang="ts">
import {Inbox,VideoOff,Search} from 'lucide-vue-next';
withDefaults(defineProps<{title:string;description?:string;icon?:string}>(),{icon:'inbox'});
</script>
<style scoped>.product-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:13px;min-height:220px;text-align:center;padding:30px;color:#8b99ad}.product-empty strong{font-size:15px;font-weight:600;color:#52627a}.product-empty p{font-size:13px;line-height:1.8;max-width:320px;margin:0;color:#8390a3}</style>
