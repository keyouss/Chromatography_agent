import { createRouter, createWebHistory } from 'vue-router'
import AppHomeView from '@/views/AppHomeView.vue'
import { routeRegistry } from '@/app/generated/registry'

export const router = createRouter({
  history: createWebHistory(),
  scrollBehavior(_to, _from, savedPosition) {
    return savedPosition ?? false
  },
  routes: [
    { path: '/', name: 'home', component: AppHomeView },
    ...routeRegistry,
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
