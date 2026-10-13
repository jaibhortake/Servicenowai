# Admin Panel Integration Guide

## Overview

The Admin Panel is a centralized management interface with provider configuration as the primary feature.

## Files

- `AdminPanel.vue` - Main admin panel component with tabbed interface
- `ProviderConfigChatbot.vue` - Provider configuration chatbot (integrated in Admin tab)

## Features

### 1. Provider Configuration Tab ⚙️

- Configure catalog providers (ServiceNow, Jira, Freshdesk, Zendesk)
- Dynamic form generation based on provider type
- Real-time connection testing
- Secure credential management

### 2. System Status Tab 📊

- MCP server status
- Neo4j database connection status
- Polling service health
- Resource utilization

### 3. System Logs Tab 📋

- Backend service logs
- Error tracking
- Debug information
- Log filtering and search

### 4. User Management Tab 👥

- User account management
- Role and permission assignment
- Activity monitoring

## Integration into Your App

### Step 1: Import AdminPanel Component

In your router or main layout:

```vue
<script setup>
import AdminPanel from "@/components/AdminPanel.vue";
</script>

<template>
  <AdminPanel />
</template>
```

### Step 2: Add Route (if using Vue Router)

```javascript
// router.ts
import AdminPanel from "@/components/AdminPanel.vue";

const routes = [
  {
    path: "/admin",
    component: AdminPanel,
    meta: { requiresAuth: true, requiresAdmin: true },
  },
];
```

### Step 3: Add Menu Item in Sidebar

```vue
<!-- In your sidebar/navigation component -->
<div class="menu-item" v-if="user.isAdmin">
  <router-link to="/admin">
    <span class="icon">⚙️</span>
    Admin Panel
  </router-link>
</div>
```

### Step 4: Protect the Route (Authentication)

```javascript
// middleware or guard
router.beforeEach((to, from, next) => {
  if (to.meta.requiresAdmin && !userStore.user?.isAdmin) {
    next("/");
  } else {
    next();
  }
});
```

## Environment Configuration

Ensure your `.env.local` has:

```
VUE_APP_API_URL=http://localhost:8099
```

Or update the API URL in `ProviderConfigChatbot.vue`:

```javascript
const API_BASE = "http://your-backend-url:8000";
```

## Tab Structure

Each tab is implemented as a separate component:

```
AdminPanel.vue
├── ProviderConfigChatbot.vue (Provider Configuration)
├── SystemStatus.vue (to be implemented)
├── SystemLogs.vue (to be implemented)
└── UserManagement.vue (to be implemented)
```

## Styling

The Admin Panel uses Tailwind-compatible utility classes and CSS Grid. Customize the color scheme by updating:

- Primary color: `#667eea` (purple)
- Secondary color: `#764ba2` (purple-dark)
- Background: `#f9fafb` (light gray)

## Extending the Admin Panel

### Adding a New Tab

1. Create a new component (e.g., `SystemStatus.vue`)
2. Add to the tabs array in `AdminPanel.vue`:

```javascript
const tabs: Tab[] = [
  // ... existing tabs
  { id: "status", label: "System Status", icon: "📊" },
];
```

3. Add the tab content:

```vue
<div v-if="activeTab === 'status'" class="tab-panel">
  <SystemStatus />
</div>
```

### Customizing ProviderConfigChatbot

The chatbot is fully customizable:

- Change colors in `<style scoped>` section
- Modify layout in `<template>` section
- Update API endpoints in `<script setup>` section

## Security Considerations

### Access Control

- Admin Panel should only be accessible to admin users
- Implement route guards to protect `/admin` path
- Check user roles before rendering sensitive components

### API Security

- All API calls include proper error handling
- Sensitive data (passwords/tokens) are masked in UI
- Environment variables used for configuration, not hardcoded secrets

### CORS

- Ensure backend allows requests from frontend origin
- Configure CORS properly for production deployment

## Responsive Design

The Admin Panel is responsive and works on:

- Desktop (1920px+)
- Tablet (768px - 1920px)
- Mobile (< 768px)

Navigation becomes scrollable on smaller screens.

## Troubleshooting

### "Cannot find module 'ProviderConfigChatbot'"

- Ensure both Vue files are in the same directory or adjust import path
- Check file names match exactly (case-sensitive on Linux/Mac)

### API calls failing

- Verify backend server is running on correct port (8000)
- Check `VUE_APP_API_URL` environment variable
- Check browser console for CORS errors

### Provider configuration not saving

- Check backend logs for errors
- Ensure all required fields are filled in the form
- Verify backend has write permissions to `provider_config.json`

## Future Enhancements

- [ ] Add real System Status component with live metrics
- [ ] Implement System Logs viewer with filtering
- [ ] Add User Management functionality
- [ ] Add Database management tools
- [ ] Add API token management
- [ ] Add Audit logs
- [ ] Add Backup/Restore functionality
