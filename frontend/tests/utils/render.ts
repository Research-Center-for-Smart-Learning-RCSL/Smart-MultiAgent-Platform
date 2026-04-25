import { mount, type ComponentMountingOptions, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory, type RouteRecordRaw } from 'vue-router'
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query'
import { i18n } from '@shared/i18n'
import type { Component } from 'vue'
import { appRoutes } from './routes'

export interface RenderOptions extends Omit<ComponentMountingOptions<unknown>, 'global'> {
  routes?: RouteRecordRaw[]
  initialRoute?: string
  queryClient?: QueryClient
}

export async function renderView(
  component: Component,
  options: RenderOptions = {},
): Promise<VueWrapper> {
  const { routes = [], initialRoute = '/', queryClient, ...mountOpts } = options

  const pinia = createPinia()
  setActivePinia(pinia)

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [...appRoutes, ...routes],
  })
  router.push(initialRoute)
  await router.isReady()

  const qc = queryClient ?? new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })

  const wrapper = mount(component, {
    ...mountOpts,
    global: {
      plugins: [pinia, router, i18n, [VueQueryPlugin, { queryClient: qc }]],
      stubs: { teleport: true },
    },
  })

  return wrapper
}
