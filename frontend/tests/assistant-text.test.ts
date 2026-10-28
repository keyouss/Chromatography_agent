import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import AssistantText from '@/modules/assistant/AssistantText.vue'

describe('AssistantText', () => {
  it('preserves bare chiral SMILES branches alongside safe Markdown links during streaming', async () => {
    const source = 'C[C@@H](N)C(=O)O 与 C[C@H](N)[C@@H](O)C [资料](https://example.com)'
    const wrapper = mount(AssistantText, { props: { text: 'C[C@@H](N)' } })
    expect(wrapper.get('.assistant-rendered').text()).toBe('C[C@@H](N)')
    await wrapper.setProps({ text: source })
    expect(wrapper.get('.assistant-rendered').text()).toContain('C[C@@H](N)C(=O)O 与 C[C@H](N)[C@@H](O)C')
    expect(wrapper.findAll('a')).toHaveLength(1)
    expect(wrapper.get('a').attributes('href')).toBe('https://example.com')
    expect(wrapper.attributes('data-source-text')).toBe(source)
    wrapper.unmount()
  })

  it('sanitizes markup and unsafe links even beside protected chiral fragments', () => {
    const wrapper = mount(AssistantText, { props: { text: 'C[C@@H](N)C(=O)O <img src=x onerror=alert(1)> [bad](javascript:alert%281%29)' } })
    expect(wrapper.get('.assistant-rendered').text()).toContain('C[C@@H](N)C(=O)O')
    expect(wrapper.find('img, script, [onerror]').exists()).toBe(false)
    expect(wrapper.find('a[href]').exists()).toBe(false)
    wrapper.unmount()
  })
  it('renders nested Markdown structures and keeps the exact Markdown source', async () => {
    const source = '# 标题\n\n> 引用段落\n\n- 父项\n  - 子项\n\n| 名称 | 数值 |\n| --- | ---: |\n| 样例 | 2 |\n\n```python\nprint("hello")\n```'
    const wrapper = mount(AssistantText, { props: { text: source } })

    expect(wrapper.find('h1').text()).toBe('标题')
    expect(wrapper.find('blockquote').text()).toContain('引用段落')
    expect(wrapper.find('ul ul li').text()).toBe('子项')
    expect(wrapper.find('table td').text()).toBe('样例')
    expect(wrapper.find('pre code').text()).toBe('print("hello")')
    expect(wrapper.attributes('data-source-text')).toBe(source)
    await wrapper.get('.assistant-source-toggle').trigger('click')
    expect(wrapper.get('pre.assistant-source').text()).toBe(source)
    wrapper.unmount()
  })

  it('removes executable markup, dangerous URLs, and remote-loading media', () => {
    const source = '<script>alert(1)</script><img src="https://example.invalid/x"><iframe src="https://example.invalid"></iframe><video src="https://example.invalid/v"></video><a href="javascript:alert(1)" onclick="alert(2)">bad</a><a href="data:text/html,evil">data</a><a href="https://example.com">safe</a>'
    const wrapper = mount(AssistantText, { props: { text: source } })

    expect(wrapper.find('script, img, iframe, video, object, svg').exists()).toBe(false)
    expect(wrapper.findAll('a')).toHaveLength(3)
    expect(wrapper.findAll('a')[0]!.attributes('href')).toBeUndefined()
    expect(wrapper.findAll('a')[1]!.attributes('href')).toBeUndefined()
    expect(wrapper.findAll('a')[2]!.attributes('href')).toBe('https://example.com')
    expect(wrapper.find('[onclick]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps streaming Markdown safe while it is incomplete and copies the source', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    const source = '**正在生成🙂\n下一行'
    const wrapper = mount(AssistantText, { props: { text: source } })

    expect(wrapper.get('.assistant-rendered').text()).toContain('正在生成🙂')
    await wrapper.get('.assistant-text-actions button:last-child').trigger('click')
    expect(writeText).toHaveBeenCalledWith(source)
    expect(wrapper.get('.assistant-text-actions').text()).toContain('已复制 Markdown')
    wrapper.unmount()
  })

  it('keeps wide table and code markup inside the styled renderer without inline styles', async () => {
    const source = `| ${'x'.repeat(600)} | Value |\n| --- | --- |\n| value | value |\n\n${'```'}\n${'x'.repeat(600)}\n${'```'}`
    const wrapper = mount(AssistantText, { props: { text: source } })
    await nextTick()
    expect(wrapper.get('.assistant-rendered table').text()).toContain('x'.repeat(600))
    expect(wrapper.get('.assistant-rendered pre code').text()).toBe('x'.repeat(600))
    expect(wrapper.get('table').attributes('style')).toBeUndefined()
    expect(wrapper.get('pre').attributes('style')).toBeUndefined()
    wrapper.unmount()
  })
})
