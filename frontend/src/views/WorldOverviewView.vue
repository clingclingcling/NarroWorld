<template>
  <div class="nw-page">
    <div class="nw-shell">
      <NarraTopBar :world-id="worldId" />
      <WorldSubnav :world-id="worldId" style="margin-top: 16px;" />

      <div v-if="overview" class="nw-grid-2" style="margin-top: 18px;">
        <section class="nw-card strong">
          <div class="nw-kicker">World Overview</div>
          <div class="nw-card-title">{{ overview.title }}</div>
          <p class="nw-subtle">{{ overview.summary }}</p>
          <div class="nw-stat-grid" style="margin-top: 18px;">
            <div class="nw-stat" v-for="(value, key) in overview.counts" :key="key">
              <div class="nw-stat-label">{{ key }}</div>
              <div class="nw-stat-value">{{ value }}</div>
            </div>
          </div>
          <div class="nw-card" style="margin-top: 16px; padding: 18px;">
            <div class="nw-kicker">Main Storyline</div>
            <p>{{ overview.main_storyline }}</p>
          </div>
          <div class="nw-actions" style="margin-top: 18px;">
            <button class="nw-btn primary" @click="router.push(`/world/${worldId}/play`)">进入剧情</button>
            <button class="nw-btn" @click="router.push(`/world/${worldId}/graph`)">查看图谱</button>
          </div>
        </section>

        <section class="nw-sidebar">
          <div class="nw-card">
            <div class="nw-kicker">Core Cast</div>
            <div class="nw-list">
              <div v-for="character in overview.characters" :key="character.id" class="nw-list-item">
                <strong>{{ character.canonical_name || character.name }}</strong>
                <p class="nw-subtle">{{ character.role || character.role_type }}</p>
              </div>
            </div>
          </div>
          <div class="nw-card">
            <div class="nw-kicker">Current State</div>
            <div class="nw-list">
              <div class="nw-list-item">
                <strong>剧情阶段</strong>
                <p class="nw-subtle">{{ overview.world_state.phase }}</p>
              </div>
              <div class="nw-list-item">
                <strong>当前场景</strong>
                <p class="nw-subtle">{{ overview.world_state.current_scene_id || '未进入' }}</p>
              </div>
              <div class="nw-list-item">
                <strong>已触发事件</strong>
                <p class="nw-subtle">{{ overview.world_state.triggered_event_ids?.length || 0 }}</p>
              </div>
            </div>
          </div>
        </section>
      </div>

      <section v-if="overview" class="nw-grid-2" style="margin-top: 18px;">
        <div class="nw-card">
          <div class="nw-kicker">Arcs</div>
          <div class="nw-list">
            <div v-for="arc in overview.arcs" :key="arc.id" class="nw-list-item">
              <strong>{{ arc.title }}</strong>
              <p class="nw-subtle">{{ arc.summary }}</p>
            </div>
          </div>
        </div>
        <div class="nw-card">
          <div class="nw-kicker">Graph Thumbnail</div>
          <div class="nw-list-item">
            <strong>{{ overview.graph_preview.nodes.length }} 个节点</strong>
            <p class="nw-subtle">{{ overview.graph_preview.edges.length }} 条边，已接入角色初始化、剧情推进与续写链路。</p>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import NarraTopBar from '../components/NarraTopBar.vue'
import WorldSubnav from '../components/WorldSubnav.vue'
import { getWorldOverview } from '../api/story'

const route = useRoute()
const router = useRouter()
const worldId = route.params.id
const overview = ref(null)

onMounted(async () => {
  const res = await getWorldOverview(worldId)
  overview.value = res.data
})
</script>
