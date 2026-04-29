<template>
  <div class="nw-page">
    <div class="nw-shell">
      <NarraTopBar />

      <section class="nw-grid-2 nw-home-grid" style="margin-top: 28px;">
        <div class="nw-card strong nw-home-hero">
          <div class="nw-kicker">NarraWorld</div>
          <div class="nw-home-display">Play the Story From Within</div>
          <div class="nw-card-title">让你直接走进故事，而不是站在外面看它运行。</div>
          <div class="nw-actions" style="margin-top: 20px;">
            <button class="nw-btn primary" @click="router.push('/create')">进入工作台</button>
          </div>
        </div>

        <div class="nw-card nw-home-intro">
          <div class="nw-kicker">简介</div>
          <p class="nw-home-intro-copy">
            上传小说、剧本或设定文档，系统会提取主角、关系图谱和剧情局面，让你直接进入第一人称叙事。
          </p>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
import NarraTopBar from '../components/NarraTopBar.vue'

const router = useRouter()
</script>
