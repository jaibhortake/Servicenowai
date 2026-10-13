<template>
  <div class="provider-config-chatbot">
    <div class="chatbot-header">
      <h2>Configuration Settings</h2>
      <p class="subtitle">Configure your catalog provider</p>
    </div>

    <!-- Current Configuration Display -->
    <div
      v-if="currentProvider"
      class="current-config"
      @click="editCurrentProvider"
    >
      <div class="config-status">
        <span class="status-label">Current Provider:</span>
        <span class="status-value">{{ getProviderName(currentProvider) }}</span>
        <span class="edit-hint">Click to edit</span>
      </div>
    </div>

    <!-- Chat Messages -->
    <div class="chat-container">
      <div class="messages">
        <!-- Initial message -->
        <div v-if="step === 'initial'" class="message bot">
          <p>Welcome! Let's configure your service provider catalog.</p>
          <p>Which provider would you like to use?</p>
        </div>

        <!-- Provider selection -->
        <div v-if="step === 'initial'" class="provider-grid">
          <button
            v-for="(provider, key) in providers"
            :key="key"
            @click="selectProvider(key)"
            class="provider-button"
            :class="{ selected: selectedProvider === key }"
          >
            <div class="provider-icon">
              {{ getProviderIcon(provider.icon) }}
            </div>
            <div class="provider-name">{{ provider.name }}</div>
            <div class="provider-description">{{ provider.description }}</div>
          </button>
        </div>

        <!-- Configuration form -->
        <div v-if="step === 'form'" class="message bot">
          <p>
            Great! I'll help you configure
            {{ getProviderName(selectedProvider) }}.
          </p>
          <p>Please provide the following information:</p>
        </div>

        <!-- Edit current form -->
        <div v-if="step === 'edit'" class="message bot">
          <p>Editing {{ getProviderName(currentProvider) }} configuration.</p>
          <p>Update the settings below:</p>
        </div>

        <!-- Form fields -->
        <div v-if="step === 'form' || step === 'edit'" class="form-container">
          <div
            v-for="question in currentQuestions"
            :key="question.id"
            class="form-group"
          >
            <label :for="question.id" class="form-label">
              {{ question.label }}
              <span v-if="question.required" class="required">*</span>
            </label>
            <input
              :id="question.id"
              v-model="formData[question.id]"
              :type="question.type"
              :placeholder="question.placeholder"
              class="form-input"
            />
            <p v-if="question.help" class="form-help">{{ question.help }}</p>
          </div>
        </div>

        <!-- Summary -->
        <div v-if="step === 'confirm'" class="message bot">
          <p>Please review your configuration:</p>
        </div>

        <!-- Edit Summary -->
        <div v-if="step === 'edit-confirm'" class="message bot">
          <p>Please review your updated configuration:</p>
        </div>

        <div
          v-if="step === 'confirm' || step === 'edit-confirm'"
          class="summary-container"
        >
          <div class="summary-item">
            <span class="summary-label">Provider:</span>
            <span class="summary-value">{{
              getProviderName(selectedProvider)
            }}</span>
          </div>
          <div
            v-for="question in currentQuestions"
            :key="question.id"
            class="summary-item"
          >
            <span class="summary-label">{{ question.label }}:</span>
            <span class="summary-value">{{
              question.type === "password" ? "••••••••" : formData[question.id]
            }}</span>
          </div>
        </div>

        <!-- Status messages -->
        <div v-if="loading" class="message bot">
          <p>⏳ {{ loadingMessage }}</p>
        </div>

        <div v-if="error" class="message error">
          <p>❌ {{ error }}</p>
        </div>

        <div v-if="success" class="message success">
          <p>✅ {{ success }}</p>
        </div>
      </div>
    </div>

    <!-- Action buttons -->
    <div class="chat-actions">
      <button
        v-if="step === 'form'"
        @click="reviewConfiguration"
        :disabled="!isFormValid"
        class="button primary"
      >
        Review Configuration
      </button>

      <button
        v-if="step === 'edit'"
        @click="editToReview"
        :disabled="!isFormValid"
        class="button primary"
      >
        Review Changes
      </button>

      <button
        v-if="step === 'edit'"
        @click="cancelEdit"
        :disabled="loading"
        class="button secondary"
      >
        Cancel
      </button>

      <button
        v-if="step === 'confirm' || step === 'edit-confirm'"
        @click="saveConfiguration"
        :disabled="loading"
        class="button primary"
      >
        {{ loading ? "Saving..." : "Save Configuration" }}
      </button>

      <button
        v-if="step === 'confirm'"
        @click="goBackToForm"
        :disabled="loading"
        class="button secondary"
      >
        Back
      </button>

      <button
        v-if="step === 'edit-confirm'"
        @click="backFromEditReview"
        :disabled="loading"
        class="button secondary"
      >
        Back
      </button>

      <button
        v-if="step === 'initial' && selectedProvider"
        @click="goToForm"
        class="button primary"
      >
        Continue
      </button>

      <button v-if="success" @click="resetForm" class="button primary">
        Configure Another Provider
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from "vue";

interface Provider {
  name: string;
  description: string;
  icon: string;
}

interface Question {
  id: string;
  label: string;
  type: string;
  placeholder?: string;
  required: boolean;
  help?: string;
}

const step = ref<"initial" | "form" | "confirm" | "edit" | "edit-confirm">(
  "initial",
);
const providers = ref<Record<string, Provider>>({});
const selectedProvider = ref<string | null>(null);
const currentProvider = ref<string | null>(null);
const formData = ref<Record<string, string>>({});
const currentQuestions = ref<Question[]>([]);
const loading = ref(false);
const loadingMessage = ref("");
const error = ref("");
const success = ref("");
const isEditingCurrent = ref(false);

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8099";

const isFormValid = computed(() => {
  return currentQuestions.value.every(
    (q) => !q.required || formData.value[q.id]?.trim(),
  );
});

async function loadProviders() {
  try {
    const response = await fetch(`${API_BASE}/api/settings/providers`);
    const data = await response.json();
    providers.value = data.providers;
    currentProvider.value = data.current;
  } catch (err) {
    error.value = "Failed to load providers";
    console.error(err);
  }
}

async function editCurrentProvider() {
  if (!currentProvider.value) return;

  isEditingCurrent.value = true;
  selectedProvider.value = currentProvider.value;

  try {
    const response = await fetch(
      `${API_BASE}/api/settings/providers/${currentProvider.value}`,
    );
    const template = await response.json();
    currentQuestions.value = template.questions || [];

    // Load current settings
    const settingsResponse = await fetch(`${API_BASE}/api/settings/current`);
    const currentSettings = await settingsResponse.json();
    formData.value = { ...currentSettings.settings };

    step.value = "edit";
  } catch (err) {
    error.value = "Failed to load provider settings";
    console.error(err);
  }
}

async function selectProvider(provider: string) {
  selectedProvider.value = provider;
  try {
    const response = await fetch(
      `${API_BASE}/api/settings/providers/${provider}`,
    );
    const template = await response.json();
    currentQuestions.value = template.questions || [];
    formData.value = {};
  } catch (err) {
    error.value = "Failed to load provider template";
    console.error(err);
  }
}

function goToForm() {
  error.value = "";
  step.value = "form";
}

function reviewConfiguration() {
  error.value = "";
  step.value = "confirm";
}

function goBackToForm() {
  error.value = "";
  step.value = "form";
}

function editToReview() {
  error.value = "";
  step.value = "edit-confirm";
}

function backFromEditReview() {
  error.value = "";
  step.value = "edit";
}

function cancelEdit() {
  isEditingCurrent.value = false;
  resetForm();
}

async function saveConfiguration() {
  if (!selectedProvider.value) return;

  loading.value = true;
  loadingMessage.value = "Saving configuration...";

  try {
    const response = await fetch(`${API_BASE}/api/settings/configure`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: selectedProvider.value,
        settings: formData.value,
      }),
    });

    const data = await response.json();

    if (!response.ok) {
      error.value = data.detail || "Failed to save configuration";
      step.value = "form";
      return;
    }

    success.value = `Provider '${getProviderName(selectedProvider.value)}' configured successfully!`;
    currentProvider.value = selectedProvider.value;

    if (isEditingCurrent.value) {
      step.value = "initial";
      isEditingCurrent.value = false;
      setTimeout(() => {
        success.value = "";
        resetForm();
      }, 2000);
    } else {
      step.value = "initial";
    }
  } catch (err) {
    error.value = "Failed to save configuration";
    console.error(err);
  } finally {
    loading.value = false;
    loadingMessage.value = "";
  }
}

function resetForm() {
  step.value = "initial";
  selectedProvider.value = null;
  currentQuestions.value = [];
  formData.value = {};
  success.value = "";
  error.value = "";
}

function getProviderName(key: string | null): string {
  if (!key) return "Unknown";
  return providers.value[key]?.name || key;
}

function getProviderIcon(icon: string): string {
  const icons: Record<string, string> = {
    servicenow: "☁️",
    jira: "⚙️",
    freshdesk: "💬",
    zendesk: "🎯",
  };
  return icons[icon] || "📦";
}

onMounted(() => {
  loadProviders();
});
</script>

<style scoped>
.provider-config-chatbot {
  max-width: 800px;
  margin: 0 auto;
  background: white;
  border-radius: 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  display: flex;
  flex-direction: column;
  height: 100%;
}

.chatbot-header {
  padding: 24px;
  border-bottom: 1px solid #e5e7eb;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border-radius: 12px 12px 0 0;
}

.chatbot-header h2 {
  margin: 0 0 8px 0;
  font-size: 24px;
  font-weight: 600;
}

.subtitle {
  margin: 0;
  opacity: 0.9;
  font-size: 14px;
}

.current-config {
  padding: 16px 24px;
  background: #f0f9ff;
  border-bottom: 1px solid #bfdbfe;
  cursor: pointer;
  transition: background-color 0.3s ease;
}

.current-config:hover {
  background: #e0f2fe;
}

.config-status {
  display: flex;
  align-items: center;
  gap: 12px;
}

.status-label {
  font-weight: 600;
  color: #1e40af;
}

.status-value {
  color: #1e40af;
  background: white;
  padding: 4px 12px;
  border-radius: 6px;
}

.edit-hint {
  margin-left: auto;
  font-size: 12px;
  color: #0369a1;
  font-weight: 500;
  opacity: 0.7;
}

.chat-container {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.messages {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.message {
  padding: 12px 16px;
  border-radius: 8px;
  max-width: 90%;
}

.message.bot {
  background: #f3f4f6;
  color: #1f2937;
}

.message p {
  margin: 0;
  line-height: 1.5;
}

.message p + p {
  margin-top: 8px;
}

.message.error {
  background: #fee2e2;
  color: #991b1b;
  border-left: 4px solid #dc2626;
}

.message.success {
  background: #dcfce7;
  color: #166534;
  border-left: 4px solid #22c55e;
}

.provider-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-top: 16px;
}

.provider-button {
  padding: 16px;
  border: 2px solid #e5e7eb;
  border-radius: 8px;
  background: white;
  cursor: pointer;
  transition: all 0.3s ease;
  text-align: center;
}

.provider-button:hover {
  border-color: #667eea;
  background: #f8f9ff;
  transform: translateY(-2px);
}

.provider-button.selected {
  border-color: #667eea;
  background: #f0f4ff;
}

.provider-icon {
  font-size: 32px;
  margin-bottom: 8px;
}

.provider-name {
  font-weight: 600;
  color: #1f2937;
  margin-bottom: 4px;
}

.provider-description {
  font-size: 12px;
  color: #6b7280;
  line-height: 1.4;
}

.form-container {
  margin-top: 16px;
  background: white;
  padding: 16px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
}

.form-group {
  margin-bottom: 16px;
}

.form-group:last-child {
  margin-bottom: 0;
}

.form-label {
  display: block;
  margin-bottom: 6px;
  font-weight: 500;
  color: #374151;
  font-size: 14px;
}

.required {
  color: #dc2626;
}

.form-input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 14px;
  transition: border-color 0.3s;
}

.form-input:focus {
  outline: none;
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

.form-help {
  margin: 6px 0 0 0;
  font-size: 12px;
  color: #6b7280;
}

.summary-container {
  margin-top: 16px;
  background: white;
  padding: 16px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
}

.summary-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 0;
  border-bottom: 1px solid #f3f4f6;
}

.summary-item:last-child {
  border-bottom: none;
}

.summary-label {
  font-weight: 500;
  color: #374151;
}

.summary-value {
  color: #1f2937;
  font-family: monospace;
  background: #f9fafb;
  padding: 4px 8px;
  border-radius: 4px;
}

.chat-actions {
  display: flex;
  gap: 12px;
  padding: 16px 24px;
  border-top: 1px solid #e5e7eb;
  background: #f9fafb;
  border-radius: 0 0 12px 12px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.button {
  padding: 10px 20px;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.3s;
}

.button.primary {
  background: #667eea;
  color: white;
}

.button.primary:hover:not(:disabled) {
  background: #5568d3;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
}

.button.secondary {
  background: white;
  color: #667eea;
  border: 1px solid #667eea;
}

.button.secondary:hover:not(:disabled) {
  background: #f8f9ff;
}

.button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
