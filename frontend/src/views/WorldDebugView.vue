<template>
  <div class="nw-page">
    <div class="nw-shell">
      <NarraTopBar :world-id="worldId" />
      <WorldSubnav :world-id="worldId" style="margin-top: 16px;" />

      <section class="nw-grid-2" style="margin-top: 18px;">
        <div class="nw-card strong">
          <div class="nw-kicker">Debug</div>
          <div class="nw-card-title">开发调试视图</div>
          <p class="nw-subtle">这里用于验证抽取质量、图谱张力、剧情候选池、play runtime 和 world state 是否合理。</p>
        </div>
        <div class="nw-card">
          <div class="nw-kicker">Quick Actions</div>
          <div class="nw-actions">
            <button class="nw-btn" @click="refreshDebug">刷新调试信息</button>
          </div>
        </div>
      </section>

      <section v-if="debugData" class="nw-grid-2" style="margin-top: 18px;">
        <div class="nw-card">
          <div class="nw-kicker">Extraction Meta</div>
          <pre style="white-space: pre-wrap;">{{ JSON.stringify(debugData.extraction_meta, null, 2) }}</pre>
        </div>
        <div class="nw-card">
          <div class="nw-kicker">World State</div>
          <pre style="white-space: pre-wrap;">{{ JSON.stringify(debugData.world_state, null, 2) }}</pre>
        </div>
        <div class="nw-card">
          <div class="nw-kicker">Planner</div>
          <pre style="white-space: pre-wrap;">{{ JSON.stringify(debugData.planner, null, 2) }}</pre>
        </div>
        <div class="nw-card">
          <div class="nw-kicker">Play State</div>
          <pre style="white-space: pre-wrap;">{{ JSON.stringify(debugData.play_state, null, 2) }}</pre>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import NarraTopBar from '../components/NarraTopBar.vue'
import WorldSubnav from '../components/WorldSubnav.vue'
import { getStoryDebug } from '../api/story'

const route = useRoute()
const worldId = route.params.id
const debugData = ref(null)

const refreshDebug = async () => {
  const res = await getStoryDebug(worldId)
  debugData.value = res.data
}

onMounted(refreshDebug)
</script>
