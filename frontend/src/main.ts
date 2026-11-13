import { createApp } from 'vue';
import { createPinia } from 'pinia';
import App from './App.vue';
import {useAuthStore} from './stores/auth';
import router from './router';
import './styles/product.css';

const app = createApp(App);
app.use(createPinia());
app.use(router);
app.mount('#app');
window.addEventListener('yuntong:session-expired', () => { useAuthStore().user = null; void router.replace('/login'); });
