<template>
  <div class="page-content">
    <h2>历史回放库</h2>
    <div class="config-card">
      <div class="empty-state">
        <p>暂无截图或录像记录...</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page-content { max-width: 800px; margin: 0 auto; padding: 32px 24px; height: 100%; overflow-y: auto; box-sizing: border-box; }
h2 { margin-top: 0; font-size: 24px; color: var(--text-main); }
.config-card { background: var(--header-bg); padding: 24px; border-radius: 12px; margin-top: 20px; border: 1px solid var(--border-color); }
.empty-state { text-align: center; padding: 40px 20px; color: var(--text-secondary); }
</style>
