import { beforeAll, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'

import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import SkillFiles from '../components/SkillFiles.vue'
import { installMessages, settle } from './kit'

function seedFiles(files: unknown[]): void {
  server.use(
    http.get('/api/projects/:pid/skills/:sid/files', () => HttpResponse.json(files)),
  )
}

const assetFile = {
  id: 'f_a',
  skill_id: 's_1',
  path: 'assets/logo.png',
  kind: 'asset',
  mime: 'image/png',
  size_bytes: 10,
  sha256: 'x',
  scan_status: 'clean',
  extracted_chars: 0,
  created_at: 't',
}
const infectedFile = {
  id: 'f_r',
  skill_id: 's_1',
  path: 'references/bad.md',
  kind: 'reference',
  mime: 'text/markdown',
  size_bytes: 5,
  sha256: 'y',
  scan_status: 'infected',
  extracted_chars: 0,
  created_at: 't',
}

describe('SkillFiles', () => {
  beforeAll(installMessages)

  it('flags the whole skill unreadable when a file is not scan-clean (AC-34)', async () => {
    seedFiles([infectedFile])
    const wrapper = await renderView(SkillFiles, {
      props: { scope: { kind: 'project', projectId: 'p_1' }, skillId: 's_1' },
    })
    await settle()
    expect(wrapper.text()).toContain('failed the malware scan')
  })

  it('gates the editor by kind: an asset binary is not editable (AC-17)', async () => {
    seedFiles([assetFile])
    const wrapper = await renderView(SkillFiles, {
      props: { scope: { kind: 'project', projectId: 'p_1' }, skillId: 's_1' },
    })
    await settle()
    const assetButton = wrapper.findAll('button').find((b) => b.text().includes('assets/logo.png'))
    expect(assetButton).toBeTruthy()
    await assetButton!.trigger('click')
    await settle(0)
    expect(wrapper.text()).toContain('cannot be edited as text')
  })

  it('offers both authoring and upload for adding a file (AC-16)', async () => {
    seedFiles([])
    const wrapper = await renderView(SkillFiles, {
      props: { scope: { kind: 'project', projectId: 'p_1' }, skillId: 's_1' },
    })
    await settle()
    expect(wrapper.text()).toContain('Author')
    expect(wrapper.text()).toContain('Upload')
    expect(wrapper.text()).toContain('Add a file')
  })
})
