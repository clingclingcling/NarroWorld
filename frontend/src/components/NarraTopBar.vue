<template>
  <header class="nw-header">
    <div class="nw-brand" @click="router.push('/')">
      <div class="nw-brand-mark">NarraWorld</div>
      <div class="nw-brand-tag">Interactive Worlds for Living Stories</div>
    </div>
    <nav class="nw-nav">
      <button class="nw-link" @click="router.push('/')">首页</button>
      <button class="nw-link" @click="router.push('/create')">创建世界</button>
      <button v-if="worldId" class="nw-link" @click="router.push(`/world/${worldId}`)">世界总览</button>
      <button class="nw-link" @click="router.push('/process/new')">高级模拟模式</button>
    </nav>
  </header>
</template>

<script setup>
import { useRouter } from 'vue-router'

defineProps({
  worldId: {
    type: String,
    default: ''
  }
})

const router = useRouter()
</script>
