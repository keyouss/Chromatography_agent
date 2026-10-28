// 用法：在 clearml-learning.html 的浏览器开发者工具 Console 中粘贴运行。
// 检查完成后恢复初始视图；不访问网络或启动训练。
(async () => {
  const results = [];
  const el = id => document.getElementById(id);
  const assert = (condition, label) => {
    if (!condition) throw new Error(label);
    results.push(label);
  };
  const filter = (value, text = '') => {
    el('status').value = value;
    el('search').value = text;
    el('search').dispatchEvent(new Event('input'));
  };
  filter('all');
  assert(el('rows').querySelectorAll('tr').length === 5, '初始列表：5 条');
  filter('completed');
  assert(el('rows').querySelectorAll('tr').length === 2, '状态筛选：2 个完成实验');
  filter('all', 'no-such-experiment');
  assert(el('rows').textContent.includes('没有匹配'), '搜索空状态');
  el('reset').click();
  document.querySelector('[data-task="demo-e51b"]').click();
  el('tab-console').click();
  assert(el('detail').textContent.includes('[error]'), '失败任务日志');
  document.querySelector('[data-task="demo-d88a"]').click();
  el('tab-scalars').click();
  assert(el('detail').textContent.includes('尚未运行'), '排队任务无指标');
  document.querySelector('[data-task="demo-b92e"]').click();
  el('tab-configuration').click();
  assert(el('detail').textContent.includes('0.0003'), '参数详情');
  el('tab-artifacts').click();
  assert(el('detail').textContent.includes('model.pt'), '已完成任务制品');
  el('tab-scalars').click();
  assert(el('detail').querySelectorAll('polyline').length === 2, '训练与验证曲线');
  for (const id of ['demo-a71c', 'demo-b92e']) document.querySelector(`[data-select="${id}"]`).click();
  assert(!el('compare').disabled, '选择两个实验启用对比');
  el('compare').click();
  assert(el('comparison').open && el('comparison-body').querySelectorAll('polyline').length === 2, '对比弹窗与曲线');
  el('close-comparison').click();
  for (const id of ['demo-a71c', 'demo-b92e']) document.querySelector(`[data-select="${id}"]`).click();
  for (const view of ['queues', 'guide', 'experiments']) {
    document.querySelector(`[data-view="${view}"]`).click();
    assert(!el(view).hidden, `导航：${view}`);
  }
  // iframe 的实际布局视口用于验证手机宽度，避免只检查媒体查询字符串。
  const frame = document.createElement('iframe');
  frame.style.cssText = 'width:390px;height:844px;position:fixed;left:-10000px;border:0';
  const loaded = new Promise((resolve, reject) => {
    frame.onload = resolve;
    frame.onerror = reject;
  });
  frame.srcdoc = '<!doctype html>' + document.documentElement.outerHTML;
  document.body.append(frame);
  try {
    await loaded;
    const doc = frame.contentDocument;
    assert(doc.documentElement.scrollWidth <= 390, '390px 窄屏无页面横向溢出');
    assert(doc.querySelector('nav').getBoundingClientRect().width <= 390, '窄屏导航可见');
  } finally { frame.remove(); }
  return { passed: results.length, results };
})();
