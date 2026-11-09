import type { SettingsSection } from '../pages/WorkspaceSettings'

export type WorkspacePage = 'Dashboard' | 'Pages' | 'Lịch đăng Page' | 'Lịch đã lưu' | 'Settings'

const paths: Record<WorkspacePage, string> = {
  Dashboard: '/',
  Pages: '/chrome/pages',
  'Lịch đăng Page': '/chrome/reels/new',
  'Lịch đã lưu': '/chrome/reels/saved',
  Settings: '/chrome/settings',
}
const settingsSections: readonly SettingsSection[] = ['app', 'bm', 'proxies', 'automation', 'history', 'recipient', 'data']

export function chromeLocation(location: Pick<Location, 'pathname' | 'search'> = window.location) {
  const page = (Object.keys(paths) as WorkspacePage[]).find(item => paths[item] === location.pathname) || 'Dashboard'
  const requested = new URLSearchParams(location.search).get('tab')
  const settingsSection = page === 'Settings' ? settingsSections.find(section => section === requested) || 'app' : null
  return { page, settingsSection }
}

export function chromeUrl(page: WorkspacePage, section: SettingsSection = 'app') {
  return paths[page] + (page === 'Settings' && section !== 'app' ? '?tab=' + section : '')
}
