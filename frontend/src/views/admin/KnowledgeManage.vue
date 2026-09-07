<template>
  <div>
    <h2>知识库管理</h2>

    <!-- 文件列表 -->
    <el-card style="margin-bottom: 18px;">
      <template #header>
        <div class="card-header">
          <span>已上传文件</span>
          <el-button type="primary" size="small" @click="refreshIndex" :loading="refreshing">
            🔄 刷新索引
          </el-button>
          <el-button type="danger" size="small" @click="handleBatchDelete" :disabled="!selectedFiles.length">
            🗑️ 批量删除{{ selectedFiles.length ? `（${selectedFiles.length}）` : '' }}
          </el-button>
        </div>
      </template>
      <el-table :data="files" style="width: 100%" v-loading="fileLoading" size="small" @selection-change="handleSelectionChange">
        <el-table-column type="selection" width="40" />
        <el-table-column prop="filename" label="文件名" />
        <el-table-column prop="size" label="大小" width="100">
          <template #default="{ row }">
            {{ formatSize(row.size) }}
          </template>
        </el-table-column>
        <el-table-column prop="modified_at" label="上传/修改时间" width="170">
          <template #default="{ row }">
            {{ formatTime(row.modified_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-button size="small" type="danger" @click="deleteFile(row.filename)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!files.length && !fileLoading" class="text-gray-400" style="padding: 18px; text-align: center;">
        暂无已上传文件
      </div>
    </el-card>

    <!-- 上传控件 -->
    <el-upload
      ref="upload"
      action=""
      :http-request="handleUpload"
      :show-file-list="false"
      multiple
      drag
      style="margin-bottom: 20px;"
    >
      <i class="el-icon-upload"></i>
      <div class="el-upload__text">拖拽或点击上传文档（PDF/TXT/Markdown）</div>
    </el-upload>

    <!-- URL 抓取 -->
    <el-card style="margin-bottom: 20px;">
      <template #header>
        <span>抓取网页 URL（针对只有网页、没有下载的文档）</span>
      </template>
      <div style="display: flex; gap: 10px;">
        <el-input v-model="fetchUrlValue" placeholder="粘贴文档网址，如 https://cn.vuejs.org/guide/" clearable />
        <el-button type="primary" @click="handleFetchUrl" :loading="fetchingUrl">抓取单页</el-button>
        <el-button type="success" @click="handleCrawlSite" :loading="crawling">抓取整站</el-button>
      </div>
    </el-card>

    <el-alert
      v-if="uploadResult"
      :title="uploadResult"
      type="success"
      show-icon
      style="margin-bottom: 20px;"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { API_BASE } from '@/config'
import { ElMessage, ElMessageBox } from 'element-plus'

// API_BASE 已改为从 @/config 统一导入
const token = localStorage.getItem("token")

const files = ref<any[]>([])
const fileLoading = ref(false)
const uploadResult = ref("")
const refreshing = ref(false)
const fetchUrlValue = ref("")
const fetchingUrl = ref(false)
const crawling = ref(false)
const selectedFiles = ref<any[]>([])

// 文件大小格式化
function formatSize(size: number) {
  if (size < 1024) return `${size}B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)}KB`
  return `${(size / 1024 / 1024).toFixed(1)}MB`
}
function formatTime(ts: number) {
  const d = new Date(ts * 1000)
  return d.toLocaleString()
}

// 加载文件列表
async function loadFiles() {
  fileLoading.value = true
  try {
    const res = await fetch(`${API_BASE}/admin/uploaded-files`, {
      headers: { Authorization: `Bearer ${token}` }
    })
    if (!res.ok) throw new Error("获取文件列表失败")
    files.value = await res.json()
  } catch (e: any) {
    ElMessage.error(e.message || "网络错误")
    files.value = []
  } finally {
    fileLoading.value = false
  }
}

// 删除文件
async function deleteFile(filename: string) {
  ElMessageBox.confirm(
    `确定要删除文件「${filename}」吗？删除后索引会自动刷新！`,
    "提示",
    { type: "warning" }
  ).then(async () => {
    const res = await fetch(`${API_BASE}/admin/uploaded-files/${encodeURIComponent(filename)}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` }
    })
    const data = await res.json()
    if (res.ok) {
      ElMessage.success(data.message || "文件已删除")
      loadFiles()
    } else {
      ElMessage.error(data.detail || "删除失败")
    }
  }).catch(() => {})
}

// 上传文件
async function handleUpload(option: any) {
  const formData = new FormData()
  for (const file of option.fileList || [option.file]) {
    formData.append("files", file)
  }
  const res = await fetch(`${API_BASE}/admin/upload-documents/`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  })
  const data = await res.json()
  if (res.ok) {
    uploadResult.value = "知识库已更新！"
    ElMessage.success("上传成功，索引已刷新")
    loadFiles()
  } else {
    uploadResult.value = data.detail || "上传失败"
    ElMessage.error(uploadResult.value)
  }
}

// 抓取网页 URL 并入库
async function handleFetchUrl() {
  const url = fetchUrlValue.value.trim()
  if (!url) {
    ElMessage.warning("请先粘贴 URL")
    return
  }
  fetchingUrl.value = true
  try {
    const res = await fetch(`${API_BASE}/admin/fetch-url`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`
      },
      body: JSON.stringify({ url })
    })
    const data = await res.json()
    if (res.ok) {
      ElMessage.success(data.message || "抓取成功")
      fetchUrlValue.value = ""
      loadFiles()
    } else {
      ElMessage.error(data.detail || "抓取失败")
    }
  } catch (e) {
    ElMessage.error("网络错误，请检查后端服务")
  } finally {
    fetchingUrl.value = false
  }
}

// 抓取整个文档站点（同目录所有页面）
async function handleCrawlSite() {
  const url = fetchUrlValue.value.trim()
  if (!url) {
    ElMessage.warning("请先粘贴网址")
    return
  }
  crawling.value = true
  try {
    const res = await fetch(`${API_BASE}/admin/crawl-site`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`
      },
      body: JSON.stringify({ url })
    })
    const data = await res.json()
    if (res.ok) {
      ElMessage.success(data.message || "抓取成功")
      fetchUrlValue.value = ""
      loadFiles()
    } else {
      ElMessage.error(data.detail || "抓取失败")
    }
  } catch (e) {
    ElMessage.error("网络错误，请检查后端服务")
  } finally {
    crawling.value = false
  }
}

// 表格多选变化
function handleSelectionChange(rows: any[]) {
  selectedFiles.value = rows
}

// 批量删除选中文件
async function handleBatchDelete() {
  if (!selectedFiles.value.length) {
    ElMessage.warning("请先勾选要删除的文件")
    return
  }
  const filenames = selectedFiles.value.map(f => f.filename)
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${filenames.length} 个文件吗？删除后索引会自动刷新！`,
      "提示",
      { type: "warning" }
    )
  } catch (e) {
    return  // 用户取消
  }
  try {
    const res = await fetch(`${API_BASE}/admin/uploaded-files/batch-delete`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`
      },
      body: JSON.stringify({ filenames })
    })
    const data = await res.json()
    if (res.ok) {
      ElMessage.success(data.message || "批量删除成功")
      selectedFiles.value = []
      loadFiles()
    } else {
      ElMessage.error(data.detail || "批量删除失败")
    }
  } catch (e) {
    ElMessage.error("网络错误，请检查后端服务")
  }
}

// 刷新索引
async function refreshIndex() {
  refreshing.value = true
  try {
    const res = await fetch(`${API_BASE}/admin/refresh-index`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` }
    })
    const data = await res.json()
    if (res.ok) {
      ElMessage.success(data.message || "索引刷新成功")
      loadFiles()
    } else {
      ElMessage.error(data.detail || "索引刷新失败")
    }
  } catch (e: any) {
    ElMessage.error(e.message || "网络错误")
  } finally {
    refreshing.value = false
  }
}

onMounted(loadFiles)
</script>

<style scoped>
.text-gray-400 {
  color: #888;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
