<template>
  <header class="nw-header">
    <div class="nw-brand" @click="router.push('/')">
      <div class="nw-brand-mark">NarraWorld</div>
      <div class="nw-brand-tag">第一人称互动叙事</div>
    </div>
    <nav class="nw-nav">
      <button v-if="showHomeLink" class="nw-link" @click="router.push('/')">首页</button>
      <button v-if="showWorkbenchLink" class="nw-link" @click="router.push('/create')">工作台</button>
      <button v-if="showWorldLink" class="nw-link" @click="router.push(`/world/${worldId}`)">当前世界</button>
    </nav>
  </header>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const props = defineProps({
  worldId: {
    type: String,
    default: ''
  }
})

const router = useRouter()
const route = useRoute()

const isHome = computed(() => route.path === '/')
const isWorkbench = computed(() => route.path === '/create')
const isWorld = computed(() => route.path.startsWith('/world/'))

const showHomeLink = computed(() => !isHome.value)
const showWorkbenchLink = computed(() => !isWorkbench.value && !isHome.value)
const showWorldLink = computed(() => Boolean(props.worldId) && !isWorld.value)
</script>
