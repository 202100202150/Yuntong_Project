<template><div class="settings-page"><div class="page-heading"><div><h1>系统设置</h1><p class="muted">账户访问、操作权限与审计记录</p></div><button class="btn" @click="refreshCurrent" :disabled="loading"><RefreshCw :size="16"/>刷新</button></div><SettingsTabs :active="activeTab"/><p v-if="error" class="error-banner" role="alert">{{error}}</p><template v-if="activeTab==='users'"><div class="users-layout"><section class="panel users-table"><div class="table-heading"><h2>用户与权限</h2><button class="btn primary" :disabled="saving" @click="newUser"><Plus :size="16"/>新增用户</button></div><label class="user-search"><Search :size="16"/><input v-model="search" placeholder="搜索账号或显示名称" aria-label="搜索用户"/></label><div class="table-wrap"><table><thead><tr><th>账号</th><th>显示名称</th><th>角色</th><th>状态</th></tr></thead><tbody><tr v-for="user in filtered" :key="user.id" :class="{selected:editingId===user.id}" @click="edit(user)" tabindex="0" @keydown.enter="edit(user)"><td>{{user.username}}</td><td>{{user.displayName}}</td><td>{{roles[user.role]||user.role}}</td><td><span class="badge" :class="user.enabled?'success':'neutral'">{{user.enabled?'启用':'停用'}}</span></td></tr></tbody></table></div><ProductEmpty v-if="!filtered.length&&!loading" title="没有匹配的用户" description="调整搜索条件或创建一个新账户" icon="search"/><p class="users-count muted">共 {{filtered.length}} 位用户</p></section><aside class="panel user-inspector"><h2>{{creating?'新增用户':'编辑用户'}}</h2><button v-if="!creating&&editingId" type="button" class="password-reset-toggle" @click="togglePasswordReset" :disabled="saving" :aria-expanded="showPasswordReset">{{showPasswordReset?'取消修改密码':'修改密码'}}</button><form v-if="creating||editingId" @submit.prevent="save"><fieldset class="user-edit-fields" :disabled="saving"><label v-if="creating" class="form-field">账号<input v-model="form.username" autocomplete="off" required minlength="3" maxlength="40" pattern="[a-zA-Z0-9._-]{3,40}" placeholder="字母、数字或 . _ -"/></label><div v-else class="account-label"><UserRound :size="17"/>{{form.username}}</div><label class="form-field">显示名称<input v-model="form.displayName" required maxlength="80"/></label><label class="form-field">角色<select v-model="form.role"><option v-for="(label,key) in roles" :key="key" :value="key">{{label}}</option></select></label><label class="enabled-switch"><span>账号状态</span><input v-model="form.enabled" type="checkbox" role="switch"/><strong>{{form.enabled?'启用':'停用'}}</strong></label><label v-if="creating||showPasswordReset" class="form-field password-field">{{creating?'登录密码':'重置密码'}}<input v-model="form.password" type="password" autocomplete="new-password" :required="creating" minlength="8" maxlength="128" :placeholder="creating?'至少 8 位字符':'留空保留原密码'"/></label><div class="permission-heading"><ShieldCheck :size="16"/><h3>角色权限</h3><span class="role-permission-note">随角色自动分配</span></div><div class="permission-list"><div v-for="permission in permissions" :key="permission.id"><span class="permission-status" :class="{allowed:allowed(permission.id)}" aria-hidden="true"><component :is="allowed(permission.id)?CheckCircle2:MinusCircle" :size="20"/></span><span>{{permission.label}}</span><small>{{allowed(permission.id)?'允许':'不可用'}}</small></div></div><div class="user-actions"><button type="button" class="btn" @click="cancel">取消</button><button class="btn primary" :disabled="saving">{{saving?'正在保存…':creating?'创建用户':'保存更改'}}</button></div></fieldset></form><ProductEmpty v-else title="选择用户查看权限" description="也可以创建新账户并分配角色"/></aside><section v-if="auth.can('audit')" class="panel audit-preview"><div class="table-heading"><h2>操作日志</h2><button class="btn" @click="openTab('audit')">查看全部 <ArrowUpRight :size="15"/></button></div><AuditTable :entries="audit.slice(0,5)"/><ProductEmpty v-if="!audit.length" title="暂无操作记录" description="账户及业务操作会在此保留日志"/></section></div></template><section v-else class="panel full-audit"><div class="table-heading"><h2>操作日志</h2><button v-if="auth.can('users')" class="btn" @click="openTab('users')">返回用户管理</button></div><AuditTable :entries="audit"/><ProductEmpty v-if="!audit.length" title="暂无操作记录"/><p class="muted audit-count">显示最近 {{audit.length}} 条操作记录</p></section></div></template>
<script setup lang="ts">
import{computed,onMounted,ref,watch}from'vue';import{useRoute,useRouter}from'vue-router';import{RefreshCw,Plus,Search,UserRound,ShieldCheck,Check,CheckCircle2,MinusCircle,ArrowUpRight}from'lucide-vue-next';import{http}from'@/api/http';import{useAuthStore}from'@/stores/auth';import{useCameraStore}from'@/stores/camera';import SettingsTabs from'@/components/SettingsTabs.vue';import ProductEmpty from'@/components/ProductEmpty.vue';import AuditTable from'@/components/AuditTable.vue';import{errorMessage}from'@/lib/product-format';
interface User{id:string;username:string;displayName:string;role:string;enabled:boolean}interface Audit{id:string;time:number;actor:string;action:string;detail:unknown}
const route=useRoute();const router=useRouter();const auth=useAuthStore();const store=useCameraStore();const roles:Record<string,string>={admin:'管理员',operator:'值班员',auditor:'审计员'};const activeTab=ref(!auth.can('users')||route.query.tab==='audit'?'audit':'users');const users=ref<User[]>([]);const audit=ref<Audit[]>([]);const search=ref('');const loading=ref(false);const saving=ref(false);const error=ref('');const editingId=ref('');const creating=ref(false);const showPasswordReset=ref(false);
const form=ref({username:'',displayName:'',role:'operator',enabled:true,password:''});const filtered=computed(()=>users.value.filter(user=>(user.username+' '+user.displayName).toLowerCase().includes(search.value.toLowerCase())));
const permissions=[{id:'monitor',label:'实时监控'},{id:'review',label:'事件复核'},{id:'playback',label:'录像回放'},{id:'export',label:'证据导出'},{id:'devices',label:'设备配置'},{id:'users',label:'用户管理'}];
const rolePermissions:Record<string,string[]>={admin:permissions.map(p=>p.id),operator:['monitor','review','playback','export'],auditor:['monitor','playback','export']};const allowed=(id:string)=>(rolePermissions[form.value.role]||[]).includes(id);
function edit(user:User){if(saving.value)return;showPasswordReset.value=false;creating.value=false;editingId.value=user.id;form.value={username:user.username,displayName:user.displayName,role:user.role,enabled:user.enabled,password:''}}
function newUser(){if(saving.value||!auth.can('users'))return;showPasswordReset.value=false;editingId.value='';creating.value=true;form.value={username:'',displayName:'',role:'operator',enabled:true,password:''}}
function cancel(){creating.value=false;const user=users.value.find(u=>u.id===editingId.value)||users.value[0];if(user)edit(user);else editingId.value=''}
function togglePasswordReset(){showPasswordReset.value=!showPasswordReset.value;if(!showPasswordReset.value)form.value.password=''}
function openTab(tab:'users'|'audit'){void router.push({path:'/settings',query:tab==='audit'?{tab:'audit'}:{}})}
async function loadAudit(){if(!auth.can('audit'))return;error.value='';try{const{data}=await http.get('/audit');audit.value=data.entries}catch(e){error.value=errorMessage(e)}}
async function load(){if(!auth.can('users')){activeTab.value='audit';await loadAudit();return}loading.value=true;error.value='';try{const{data}=await http.get('/users');users.value=data.users;const user=users.value.find(u=>u.id===editingId.value)||users.value[0];if(user&&!creating.value)edit(user);await loadAudit()}catch(e){error.value=errorMessage(e)}finally{loading.value=false}}
async function save(){if(saving.value||!auth.can('users'))return;saving.value=true;error.value='';let saved=false;try{if(creating.value){const{data}=await http.post('/users',{username:form.value.username,displayName:form.value.displayName,role:form.value.role,password:form.value.password,enabled:form.value.enabled});creating.value=false;editingId.value=data.user.id}else{await http.patch('/users/'+encodeURIComponent(editingId.value),{displayName:form.value.displayName,role:form.value.role,enabled:form.value.enabled,...(form.value.password?{password:form.value.password}:{})})}saved=true;store.toast('用户设置已保存','success');form.value.password='';showPasswordReset.value=false;if(editingId.value===auth.user?.id){await auth.refresh();if(!auth.can('users')){await router.replace(auth.can('audit')?'/settings?tab=audit':'/home');return}}}catch(e){error.value=errorMessage(e)}finally{saving.value=false}if(saved)await load()}
async function refreshCurrent(){if(activeTab.value==='audit'){loading.value=true;error.value='';try{await loadAudit()}finally{loading.value=false}}else await load()}
watch(()=>route.query.tab,value=>{activeTab.value=!auth.can('users')||value==='audit'?'audit':'users';void refreshCurrent()});onMounted(()=>{if(activeTab.value==='audit')void loadAudit();else void load()});
</script><style scoped>
.table-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 15px;
  margin-bottom: 22px;
}

.table-heading h2 {
  font-size: 18px;
  margin: 0;
}

tbody tr {
  cursor: pointer;
}

tbody tr:hover {
  background: #f6f9fe;
}

tbody tr.selected {
  background: #eaf2ff;
}

.audit-count {
  font-size: 12px;
  margin: 25px 0 0;
}

.settings-page>.page-heading {
  height: 76px;
  min-height: 76px;
  margin: 0;
  padding: 0 16px;
}

.settings-page>.page-heading p {
  display: none;
}

.settings-page :deep(.settings-tabs a) {
  font-size: 18px;
}

.settings-page :deep(.settings-tabs button) {
  font-size: 18px;
}

.full-audit {
  min-width: 0;
  padding: 18px 20px;
  min-height: 0;
}

.users-table>.table-heading {
  display: contents;
}

.users-count {
  font-size: 12px;
  margin: 22px 0 0;
  display: none;
}

.users-table>.product-empty {
  grid-column: 1/-1;
}

.user-inspector form {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

.account-label {
  gap: 10px;
  align-items: center;
  background: #f5f8fc;
  border-radius: 7px;
  padding: 13px;
  color: #69809e;
  font-size: 13px;
  margin-bottom: 22px;
  display: none;
}

.enabled-switch input:before {
  content: '';
  position: absolute;
  top: 3px;
  left: 3px;
  background: white;
  border-radius: 50%;
  box-shadow: 0 1px 3px #18344d30;
  transition: transform .18s;
  width: 23px;
  height: 23px;
}

.enabled-switch input:checked:before {
  transform: translateX(23px);
}

.permission-heading svg {
  display: none;
}

.permission-list {
  margin: 8px 0 0;
}

.permission-check svg {
  width: 17px;
  height: 17px;
}

.user-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: auto;
  gap: 17px;
  padding-top: 25px;
}

.audit-preview :deep(.audit-detail) {
  display: none;
}

.audit-preview :deep(.product-empty) {
  min-height: 120px;
}

.settings-page :deep(.btn) {
  font-size: 16px;
}

.settings-page {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  color: #111827;
}

.settings-page :deep(.settings-tabs) {
  height: 60px;
  min-height: 60px;
  border: 1px solid #dce3f0;
  margin: 0 0 16px;
  padding: 0 20px;
  gap: 22px;
  background: linear-gradient(110deg,#fbfcff,#f5f8fe);
  border-color: #dce3ee;
  border-radius: 12px;
  box-shadow: 0 3px 7px #2e4b8305;
}

.users-layout {
  display: grid;
  gap: 16px 8px;
  flex: 1;
  min-height: 0;
  grid-template-columns: minmax(0,2fr) minmax(420px,.99fr);
  grid-template-rows: 420px minmax(255px,1fr);
}

.users-table {
  min-width: 0;
  min-height: 0;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px 10px;
  grid-template-rows: 36px 50px minmax(0,1fr);
  padding: 18px 20px;
}

.user-inspector {
  min-width: 0;
  min-height: 0;
  grid-column: 2;
  grid-row: 1/3;
  display: flex;
  flex-direction: column;
  position: relative;
  padding: 18px 20px;
}

.audit-preview {
  min-width: 0;
  min-height: 0;
  grid-column: 1;
  grid-row: 2;
  overflow: auto;
  padding: 18px 20px;
}

.users-table>.table-heading h2 {
  grid-column: 1/-1;
  grid-row: 1;
  font-size: 24px;
  font-weight: 700;
  color: #111827;
}

.user-inspector h2 {
  margin-bottom: 25px;
  margin: 0 0 22px;
  font-size: 24px;
  font-weight: 700;
  color: #111827;
}

.audit-preview h2 {
  font-size: 24px;
  font-weight: 700;
  color: #111827;
}

.full-audit h2 {
  font-size: 24px;
  font-weight: 700;
  color: #111827;
}

.users-table>.table-heading>.btn {
  grid-column: 1;
  grid-row: 2;
  justify-self: start;
  align-self: start;
  height: 40px;
  min-width: 140px;
  font-size: 17px;
  font-weight: 600;
}

.user-search {
  display: flex;
  align-items: center;
  border: 1px solid #dbe4ef;
  gap: 10px;
  margin-bottom: 23px;
  grid-column: 2;
  grid-row: 2;
  margin: 0;
  align-self: start;
  height: 39px;
  padding: 9px 12px;
  font-size: 16px;
  color: #5f6b82;
  border-color: #ccd6e5;
  background: #fbfcff;
  border-radius: 8px;
}

.user-search input {
  flex: 1;
  min-width: 0;
  border: 0;
  outline: none;
  font: inherit;
  background: transparent;
  font-size: 16px;
  font-weight: 500;
  color: #111827;
}

.users-table>.table-wrap {
  grid-column: 1/-1;
  grid-row: 3;
  align-self: start;
  border: 1px solid #dce3ef;
  border-color: #d5ddeb;
  border-radius: 8px;
  max-height: 260px;
}

.users-table table {
  table-layout: fixed;
}

.users-table th {
  font-size: 18px;
  font-weight: 600;
  color: #111827;
  background: linear-gradient(#f0f3fa,#eaf0f9);
  padding: 10px 21px;

  line-height: 1.35;
}

.users-table td {
  font-size: 18px;
  font-weight: 500;
  color: #111827;
  padding: 11px 21px;
  border-color: #dce3ed;

  line-height: 1.35;
}

.users-table th:nth-child(1) {
  width: 28%;
}

.users-table th:nth-child(2) {
  width: 30%;
}

.users-table th:nth-child(3) {
  width: 27%;
}

.users-table th:nth-child(4) {
  width: 15%;
}

.users-table tr.selected {
  background: linear-gradient(110deg,#d5e5ff,#e0ebff);
  outline: 1px solid #3980ff;
  outline-offset: -1px;
}

.users-table .badge {
  font-size: 17px;
  font-weight: 600;
  border: 1px solid #c1ebd0;
  border-radius: 7px;
  padding: 3px 9px;

  line-height: 1.2;
}

.user-inspector .form-field {
  display: grid;
  grid-template-columns: 117px minmax(0,1fr);
  align-items: center;
  gap: 12px;
  margin: 0 0 21px;
  font-size: 18px;
  font-weight: 550;
  color: #111827;
  margin-bottom: 18px;
}

.user-inspector .form-field input {
  height: 39px;
  padding: 8px 13px;
  font-size: 18px;
  font-weight: 600;
  border-color: #c7d0df;
  background: #fbfcff;
  border-radius: 8px;
}

.user-inspector .form-field select {
  height: 39px;
  padding: 8px 13px;
  font-size: 18px;
  font-weight: 600;
  border-color: #c7d0df;
  background: #fbfcff;
  border-radius: 8px;
}

.enabled-switch {
  display: flex;
  align-items: center;
  margin: 0 0 22px;
  height: 36px;
  font-size: 18px;
  gap: 16px;
  margin-bottom: 19px;
}

.enabled-switch>span {
  margin-right: auto;
  width: 117px;
  flex-shrink: 0;
  margin: 0;
  color: #111827;
  font-weight: 500;
}

.enabled-switch strong {
  color: #111827;
  font-weight: 500;
}

.enabled-switch input {
  appearance: none;
  position: relative;
  cursor: pointer;
  transition: background .18s;
  width: 52px;
  height: 29px;
  border-radius: 17px;
  flex-shrink: 0;
  background: #c7cfdb;
}

.enabled-switch input:checked {
  background: #0868ff;
}

.permission-heading {
  border-top: 1px solid #e5ebf3;
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 25px;
  margin: 0;
  padding-top: 20px;
  border-color: #d9e0eb;
  color: #111827;
}

.permission-heading h3 {
  margin: 0;
  font-size: 20px;
  font-weight: 650;
}

.permission-list>div {
  display: flex;
  align-items: center;
  border-bottom: 1px solid #edf1f6;
  gap: 15px;
  font-size: 18px;
  font-weight: 600;
  color: #111827;
  padding: 9px 10px;
  min-height: 44px;
  border-color: #dce3ed;
}

.permission-list small {
  margin-left: auto;
  font-size: 17px;
  font-weight: 500;
  color: #5f6b82;
}

.permission-check {
  border: 1px solid #cbd6e5;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 5px;
  border-color: #bfcadb;
  background: #fbfcff;
  box-shadow: inset 0 1px 2px #52608012;
}

.permission-check.allowed {
  color: white;
  background: #0868ff;
  border-color: #0868ff;
  box-shadow: none;
}

.user-actions .btn {
  height: 44px;
  font-size: 17px;
  font-weight: 650;
  min-width: 104px;
}

.user-actions .primary {
  min-width: 142px;
}

.password-reset-toggle {
  position: absolute;
  right: 22px;
  top: 25px;
  border: 0;
  background: none;
  cursor: pointer;
  padding: 2px;
  color: #5f6b82;
  font-size: 13px;
  font-weight: 500;
}

.audit-preview>.table-heading {
  margin-bottom: 14px;
}

.audit-preview :deep(table) {
  table-layout: fixed;
}

.audit-preview :deep(th) {
  font-size: 17px;
  font-weight: 600;
  color: #111827;
  background: linear-gradient(#f0f3fa,#eaf0f9);
  padding: 10px 16px;
}

.audit-preview :deep(td) {
  font-size: 17px;
  font-weight: 500;
  color: #111827;
  padding: 10px 16px;
  border-color: #dce3ed;
}

.audit-preview :deep(th:first-child) {
  width: 30%;
}

.audit-preview :deep(th:nth-child(2)) {
  width: 23%;
}

.audit-preview :deep(th:nth-child(3)) {
  width: 47%;
}

.audit-preview :deep(.table-wrap) {
  border: 1px solid #dce3ef;
  border-radius: 7px;
  border-color: #d5ddeb;
}

@media (max-width:1250px) {
  .users-layout {
    grid-template-columns: minmax(0,1.65fr) minmax(370px,1fr);
  }

  .user-inspector .form-field {
    grid-template-columns: 90px minmax(0,1fr);
  }

  .enabled-switch>span {
    width: 90px;
  }

  .users-table td {
    font-size: 15px;
    padding: 14px;
  }

  .users-table th {
    font-size: 15px;
    padding: 14px;
  }

  .audit-preview :deep(td) {
    font-size: 14px;
    padding: 13px;
  }

  .audit-preview :deep(th) {
    font-size: 14px;
    padding: 13px;
  }

  .permission-list>div {
    font-size: 16px;
  }
}

@media (max-width:1000px) {
  .settings-page {
    height: auto;
  }

  .users-layout {
    grid-template-columns: 1fr;
    grid-template-rows: 420px auto auto;
  }

  .user-inspector {
    grid-column: 1;
    grid-row: 2;
    min-height: 650px;
  }

  .audit-preview {
    grid-column: 1;
    grid-row: 3;
    min-height: 270px;
  }
}
.user-edit-fields{border:0;padding:0;margin:0;min-width:0;display:flex;flex-direction:column;flex:1}.permission-status{width:22px;height:22px;display:grid;place-items:center;color:#99a5b8}.permission-status.allowed{color:#0868ff}.role-permission-note{margin-left:auto;font-size:13px;font-weight:500;color:#5f6b82}
</style>
