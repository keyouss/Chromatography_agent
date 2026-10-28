const labels: Record<string, string> = {
  QUEUED: '排队中', RUNNING: '运行中', WAITING_CONFIRMATION: '等待确认', CANCEL_REQUESTED: '取消请求中',
  SUCCEEDED: '已完成', PARTIAL: '部分完成', FAILED: '失败', CANCELED: '已取消', TIMED_OUT: '已超时', INTERRUPTED: '已中断',
  PROPOSED: '已提议', VALIDATING: '校验中', REJECTED: '已拒绝', DRAFT: '草稿', VALID: '有效', INVALID: '无效', REGISTERED: '已登记',
}

export function statusLabel(status: string): string { return labels[status] ?? status }
