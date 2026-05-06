<template>
  <div class="nw-tabs">
    <button
      v-for="item in items"
      :key="item.path"
      class="nw-tab"
      :class="{ active: route.path === item.path }"
      @click="router.push(item.path)"
    >
      {{ item.label }}
    </button>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const props = defineProps({
  worldId: {
    type: String,
    required: true
  }
})

const route = useRoute()
const router = useRouter()

const items = computed(() => ([
  { label: '总览', path: `/world/${props.worldId}` },
  { label: '剧情游玩', path: `/world/${props.worldId}/play` },
  { label: '续写', path: `/world/${props.worldId}/continuation` },
  { label: '调试', path: `/world/${props.worldId}/debug` }
]))
</script>
