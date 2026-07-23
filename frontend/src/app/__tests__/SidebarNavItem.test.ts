// Active-state matching: a section stays lit on its detail routes, an `exact`
// list item does not stay lit on deeper paths (the two-items-active bug from
// code review), and a shorter route does not match a sibling sharing a prefix.

import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { HomeIcon } from '@heroicons/vue/24/outline'

import SidebarNavItem from '../components/SidebarNavItem.vue'

async function mountAt(path: string, props: { to: string; exact?: boolean }) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:rest(.*)*', component: { template: '<div />' } }],
  })
  router.push(path)
  await router.isReady()
  return mount(SidebarNavItem, {
    props: { icon: HomeIcon, label: 'X', ...props },
    global: { plugins: [router] },
  })
}

const isActive = (w: Awaited<ReturnType<typeof mountAt>>) =>
  w.find('a').classes().includes('nav-item--active')

describe('SidebarNavItem active state', () => {
  it('keeps a section active on its own detail routes', async () => {
    expect(isActive(await mountAt('/projects/abc/agents/xyz', { to: '/projects/abc/agents' }))).toBe(true)
  })

  it('does not keep an exact list item active on a deeper path (no double highlight)', async () => {
    expect(isActive(await mountAt('/projects/abc/agents', { to: '/projects', exact: true }))).toBe(false)
    // ...and the section item at the same path IS active, so only one lights up.
    expect(isActive(await mountAt('/projects/abc/agents', { to: '/projects/abc/agents' }))).toBe(true)
  })

  it('does not match a sibling route that merely shares a string prefix', async () => {
    expect(isActive(await mountAt('/projects/abc/agent-groups', { to: '/projects/abc/agents' }))).toBe(false)
  })
})
