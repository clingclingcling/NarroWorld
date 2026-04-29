<template>
  <div class="nw-page">
    <div class="nw-shell">
      <NarraTopBar :world-id="worldId" />
      <WorldSubnav :world-id="worldId" style="margin-top: 16px;" />

      <section class="nw-grid-2" style="margin-top: 18px;">
        <div class="nw-card strong">
          <div class="nw-kicker">Continuation Engine</div>
          <div class="nw-card-title">结局后续写</div>
          <p class="nw-subtle">从当前 world state、图谱冲突、隐藏秘密与玩家行为轨迹生成下一篇章方向。</p>
          <div class="nw-actions" style="margin-top: 18px;">
            <button class="nw-btn primary" @click="refreshContinuation">重新生成下一篇章</button>
          </div>
        </div>

        <div class="nw-card" v-if="continuation">
          <div class="nw-kicker">Next Chapter</div>
          <div class="nw-card-title">下一篇章概览</div>
          <p class="nw-subtle">{{ continuation.next_chapter_overview }}</p>
        </div>
      </section>

      <section v-if="continuation" class="nw-grid-3" style="margin-top: 18px;">
        <div class="nw-card">
          <div class="nw-kicker">New Conflicts</div>
          <div class="nw-list">
            <div v-for="item in continuation.new_conflicts" :key="item" class="nw-list-item">{{ item }}</div>
          </div>
        </div>
        <div class="nw-card">
          <div class="nw-kicker">New Tasks</div>
          <div class="nw-list">
            <div v-for="item in continuation.new_tasks" :key="item" class="nw-list-item">{{ item }}</div>
          </div>
        </div>
        <div class="nw-card">
          <div class="nw-kicker">New Event Chain</div>
          <div class="nw-list">
            <div v-for="item in continuation.new_event_chain" :key="item" class="nw-list-item">{{ item }}</div>
          </div>
        </div>
      </section>

      <section v-if="continuation?.next_narrative_blocks?.length" class="nw-card" style="margin-top: 18px;">
        <div class="nw-kicker">Next Playable Beats</div>
        <div class="nw-card-title">下一篇章会如何长出来</div>
        <div class="nw-list" style="margin-top: 16px;">
          <div v-for="block in continuation.next_narrative_blocks" :key="block.id" class="nw-list-item">
            <strong>{{ block.title }}</strong>
            <p class="nw-subtle" style="margin-top: 6px;">{{ block.situation }}</p>
            <p class="nw-subtle">冲突：{{ block.conflict }}</p>
            <p class="nw-subtle">目标：{{ block.objective }}</p>
            <p class="nw-subtle">风险：{{ block.risk }}</p>
          </div>
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
import { generateStoryContinuation } from '../api/story'

const route = useRoute()
const worldId = route.params.id
const continuation = ref(null)

const refreshContinuation = async () => {
  const res = await generateStoryContinuation(worldId)
  continuation.value = res.data
}

onMounted(refreshContinuation)
</script>
