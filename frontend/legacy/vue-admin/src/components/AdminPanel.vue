<template>
  <div class="admin-panel-container">
    <!-- Admin Header -->
    <div class="admin-header">
      <h1>Admin Panel</h1>
      <p class="subtitle">Manage system settings and configurations</p>
    </div>

    <!-- Navigation Tabs -->
    <div class="admin-nav">
      <button
        v-for="tab in tabs"
        :key="tab.id"
        @click="activeTab = tab.id"
        :class="{ active: activeTab === tab.id }"
        class="nav-button"
      >
        <span class="icon">{{ tab.icon }}</span>
        {{ tab.label }}
      </button>
    </div>

    <!-- Tab Content -->
    <div class="admin-content">
      <!-- Provider Configuration Tab -->
      <div v-if="activeTab === 'providers'" class="tab-panel">
        <ProviderConfigChatbot />
      </div>

      <!-- System Status Tab -->
      <div v-if="activeTab === 'status'" class="tab-panel">
        <SystemStatus />
      </div>

      <!-- Logs Tab -->
      <div v-if="activeTab === 'logs'" class="tab-panel">
        <SystemLogs />
      </div>

      <!-- Users Tab -->
      <div v-if="activeTab === 'users'" class="tab-panel">
        <UserManagement />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import ProviderConfigChatbot from "./ProviderConfigChatbot.vue";

interface Tab {
  id: string;
  label: string;
  icon: string;
}

const activeTab = ref<string>("providers");

const tabs: Tab[] = [
  { id: "providers", label: "Provider Configuration", icon: "⚙️" },
  { id: "status", label: "System Status", icon: "📊" },
  { id: "logs", label: "System Logs", icon: "📋" },
  { id: "users", label: "User Management", icon: "👥" },
];

// Placeholder components for future implementation
const SystemStatus = {
  template: `<div class="placeholder">System Status will be implemented here</div>`,
};

const SystemLogs = {
  template: `<div class="placeholder">System Logs will be implemented here</div>`,
};

const UserManagement = {
  template: `<div class="placeholder">User Management will be implemented here</div>`,
};
</script>

<style scoped>
.admin-panel-container {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #f9fafb;
}

.admin-header {
  padding: 32px 24px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.admin-header h1 {
  margin: 0 0 8px 0;
  font-size: 32px;
  font-weight: 700;
}

.subtitle {
  margin: 0;
  opacity: 0.95;
  font-size: 16px;
}

.admin-nav {
  display: flex;
  gap: 8px;
  padding: 16px 24px;
  background: white;
  border-bottom: 1px solid #e5e7eb;
  overflow-x: auto;
}

.nav-button {
  padding: 10px 20px;
  border: 2px solid transparent;
  background: white;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  color: #6b7280;
  display: flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
  transition: all 0.3s;
}

.nav-button:hover {
  color: #667eea;
  background: #f3f4f6;
}

.nav-button.active {
  color: #667eea;
  border-color: #667eea;
  background: #f0f4ff;
}

.nav-button .icon {
  font-size: 16px;
}

.admin-content {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.tab-panel {
  animation: fadeIn 0.3s ease;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.placeholder {
  padding: 40px;
  text-align: center;
  background: white;
  border-radius: 12px;
  border: 2px dashed #e5e7eb;
  color: #9ca3af;
  font-size: 16px;
}

/* Responsive */
@media (max-width: 768px) {
  .admin-header {
    padding: 24px 16px;
  }

  .admin-header h1 {
    font-size: 24px;
  }

  .admin-nav {
    padding: 12px 16px;
  }

  .nav-button {
    padding: 8px 16px;
    font-size: 12px;
  }

  .admin-content {
    padding: 16px;
  }
}
</style>
