<template>
  <div class="nw-page">
    <div class="nw-shell">
      <NarraTopBar :world-id="worldId" />
      <WorldSubnav :world-id="worldId" style="margin-top: 16px;" />

      <section class="nw-grid-2" style="margin-top: 18px;">
        <div class="nw-card strong">
          <div class="nw-kicker">Characters</div>
          <div class="nw-list">
            <button
              v-for="character in characters"
              :key="character.id"
              class="nw-list-item"
              style="text-align: left; cursor: pointer;"
              @click="selectedCharacter = character"
            >
              <strong>{{ character.canonical_name || character.name }}</strong>
              <p class="nw-subtle">{{ character.role || character.role_type }} · Importance {{ character.importance_score }}</p>
            </button>
          </div>
        </div>
        <div class="nw-sidebar">
          <div class="nw-card">
            <div class="nw-kicker">角色详情</div>
            <template v-if="selectedCharacter">
              <div class="nw-card-title">{{ selectedCharacter.canonical_name || selectedCharacter.name }}</div>
              <p class="nw-subtle">{{ selectedCharacter.summary || selectedCharacter.persona }}</p>
              <div class="nw-pill-row" style="margin-top: 12px;">
                <span v-for="alias in selectedCharacter.aliases" :key="alias" class="nw-pill">{{ alias }}</span>
              </div>
              <div class="nw-list" style="margin-top: 12px;">
                <div class="nw-list-item">
                  <strong>动机</strong>
                  <p class="nw-subtle">{{ selectedCharacter.motivation }}</p>
                </div>
                <div class="nw-list-item">
                  <strong>目标</strong>
                  <p class="nw-subtle">{{ selectedCharacter.goals?.join(' / ') }}</p>
                </div>
                <div class="nw-list-item">
                  <strong>隐藏信息</strong>
                  <p class="nw-subtle">{{ selectedCharacter.hidden_info?.join(' / ') || '暂无' }}</p>
                </div>
                <div class="nw-list-item">
                  <strong>运行时状态</strong>
                  <pre style="white-space: pre-wrap;">{{ JSON.stringify(selectedCharacter.runtime || {}, null, 2) }}</pre>
                </div>
                <div class="nw-list-item">
                  <strong>说话风格 / 风险偏好</strong>
                  <p class="nw-subtle">{{ selectedCharacter.runtime?.speech_style || '未生成' }} / {{ selectedCharacter.runtime?.risk_profile || '未生成' }}</p>
                </div>
                <div class="nw-list-item">
                  <strong>价值边界</strong>
                  <p class="nw-subtle">{{ selectedCharacter.runtime?.value_guardrails?.join(' / ') || '暂无' }}</p>
                </div>
                <div class="nw-list-item">
                  <strong>证据链</strong>
                  <pre style="white-space: pre-wrap;">{{ JSON.stringify(selectedCharacter.evidence || [], null, 2) }}</pre>
                </div>
              </div>
            </template>
            <p v-else class="nw-subtle">选择左侧角色，查看其别名、动机、秘密、运行时状态和图谱上下文。</p>
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
import { getStoryCharacters } from '../api/story'

const route = useRoute()
const worldId = route.params.id
const characters = ref([])
const selectedCharacter = ref(null)

onMounted(async () => {
  const res = await getStoryCharacters(worldId)
  characters.value = res.data || []
  selectedCharacter.value = characters.value[0] || null
})
</script>
