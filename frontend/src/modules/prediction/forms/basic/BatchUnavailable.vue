<script setup lang="ts">
interface Props { typeLabel: string; reason?: string }
const props = defineProps<Props>()
</script>
<template><section class="panel empty-state"><span class="eyebrow">BATCH · CONTRACT REQUIRED</span><h2>{{ props.typeLabel }} 批量预测</h2><p>{{ props.reason || '后端批量 DTO 与任务接口尚未冻结。表单不会向未发布路径提交，也不会用单次请求在浏览器逐行调度。' }}</p></section></template>
