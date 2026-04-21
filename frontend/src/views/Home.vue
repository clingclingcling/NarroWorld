<template>
  <div class="nw-page">
    <div class="nw-shell">
      <NarraTopBar />

      <section class="nw-hero" style="margin-top: 20px;">
        <div class="nw-card strong">
          <div class="nw-kicker">NarraWorld</div>
          <h1 class="nw-hero-title">
            把小说、剧本、设定文档
            <span>变成可互动的故事世界</span>
          </h1>
          <p class="nw-subtle" style="margin-top: 18px; max-width: 760px;">
            上传任意故事文本，NarraWorld 会完成角色抽取、叙事图谱生成、世界状态初始化与剧情运行，
            然后让你真正进入其中，与角色共演、改变节点、续写新篇章。
          </p>
          <div class="nw-actions" style="margin-top: 26px;">
            <button class="nw-btn primary" @click="router.push('/create')">上传故事</button>
            <button class="nw-btn" @click="openSampleWorld">进入样例世界</button>
            <button class="nw-btn" @click="openGraphDemo">查看图谱演示</button>
          </div>
          <div class="nw-pill-row" style="margin-top: 18px;">
            <span class="nw-pill">故事导入</span>
            <span class="nw-pill">图谱生成</span>
            <span class="nw-pill">角色运行</span>
            <span class="nw-pill">玩家互动</span>
            <span class="nw-pill">续写篇章</span>
          </div>
        </div>

        <div class="nw-card">
          <div class="nw-kicker">四步体验</div>
          <div class="nw-list">
            <div class="nw-list-item">
              <strong>1. 导入故事</strong>
              <p class="nw-subtle">支持小说、剧本、设定文档，自动抽取角色、关系、事件、线索与秘密。</p>
            </div>
            <div class="nw-list-item">
              <strong>2. 生成世界</strong>
              <p class="nw-subtle">把结构化资产映射到统一图谱与 world state，让叙事可检索、可回溯、可运行。</p>
            </div>
            <div class="nw-list-item">
              <strong>3. 进入剧情</strong>
              <p class="nw-subtle">以聊天流而不是控制台体验剧情，角色会主动发消息，关键节点会弹出选项。</p>
            </div>
            <div class="nw-list-item">
              <strong>4. 续写世界</strong>
              <p class="nw-subtle">主线收束后继承既有关系、秘密和玩家影响，生成自然延伸的新篇章。</p>
            </div>
          </div>
        </div>
      </section>

      <section class="nw-grid-3" style="margin-top: 20px;">
        <div class="nw-card">
          <div class="nw-kicker">故事抽取</div>
          <div class="nw-card-title">稳态角色切分</div>
          <p class="nw-subtle">通过实体初抽取、别名合并、重要度判断和 schema 校验，减少脏角色与重复角色。</p>
        </div>
        <div class="nw-card">
          <div class="nw-kicker">图谱层</div>
          <div class="nw-card-title">Zep 风格叙事图谱</div>
          <p class="nw-subtle">人物关系、事件因果、线索归属、秘密张力与玩家动作都会写入统一图谱层。</p>
        </div>
        <div class="nw-card">
          <div class="nw-kicker">剧情运行</div>
          <div class="nw-card-title">聊天流驱动体验</div>
          <p class="nw-subtle">角色消息、场景变化、线索解锁和关键节点选项以戏剧节奏逐步推送。</p>
        </div>
      </section>

      <section class="nw-grid-2" style="margin-top: 20px;">
        <div class="nw-card strong">
          <div class="nw-kicker">场景演示</div>
          <div class="nw-card-title">悬疑案件世界线演化</div>
          <div class="nw-list">
            <div class="nw-list-item">
              <strong>00:14</strong>
              <p class="nw-subtle">匿名短信触发主线，调查者与知情人之间出现第一层信任裂缝。</p>
            </div>
            <div class="nw-list-item">
              <strong>01:02</strong>
              <p class="nw-subtle">系统叙事消息推送：监控强度上升，关键角色失联 12 分钟。</p>
            </div>
            <div class="nw-list-item">
              <strong>02:11</strong>
              <p class="nw-subtle">新线索解锁，玩家可决定相信谁、公开什么、压下什么。</p>
            </div>
          </div>
        </div>
        <div class="nw-card">
          <div class="nw-kicker">主入口</div>
          <div class="nw-footer-nav">
            <button class="nw-btn primary" @click="router.push('/create')">进入工作台</button>
            <button class="nw-btn" @click="openSampleWorld">进入剧情模式</button>
            <a class="nw-btn" href="README-ZH.md" target="_blank">查看文档</a>
          </div>
          <p class="nw-subtle" style="margin-top: 18px;">
            原有多智能体模拟能力依旧保留，但它现在作为底层技术与高级模式存在，不再是产品首页主叙事。
          </p>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import NarraTopBar from '../components/NarraTopBar.vue'
import { listStories } from '../api/story'

const router = useRouter()
const latestWorldId = ref('')

const loadLatestWorld = async () => {
  const res = await listStories(1)
  latestWorldId.value = res.data?.[0]?.story_id || ''
}

const openSampleWorld = () => {
  if (latestWorldId.value) {
    router.push(`/world/${latestWorldId.value}`)
    return
  }
  router.push('/create')
}

const openGraphDemo = () => {
  if (latestWorldId.value) {
    router.push(`/world/${latestWorldId.value}/graph`)
    return
  }
  router.push('/create')
}

onMounted(loadLatestWorld)
</script>
