# Quick Start: Adding Admin Panel to Your UI

Based on your screenshot, here's how to integrate the Admin Panel:

## Current UI Structure

```
Left Sidebar:
├── AI Service Portal
├── New Request (active)
├── My Tickets
└── Admin Panel (click here)

Main Content:
└── New Service Request page
```

## Step 1: Update Your Sidebar Navigation

In your sidebar/navigation component, update the "Admin Panel" menu item to navigate to the admin page:

```vue
<!-- In your Sidebar.vue or Navigation.vue -->
<template>
  <div class="sidebar">
    <!-- ... other menu items ... -->

    <router-link to="/admin" class="menu-item">
      <span class="icon">⚙️</span>
      <span>Admin Panel</span>
    </router-link>
  </div>
</template>
```

## Step 2: Add Route to Router

In your `router.ts` or `router.js`:

```javascript
import AdminPanel from "@/components/AdminPanel.vue";

const routes = [
  // ... existing routes ...
  {
    path: "/admin",
    component: AdminPanel,
    name: "admin",
    meta: {
      title: "Admin Panel",
      requiresAuth: true,
      requiresAdmin: true, // Only admins can access
    },
  },
];
```

## Step 3: (Optional) Add Authentication Guard

In your router setup:

```javascript
// Protect admin routes
router.beforeEach((to, from, next) => {
  const userStore = useUserStore(); // or your auth store

  if (to.meta.requiresAdmin) {
    if (userStore.user?.role === "admin") {
      next();
    } else {
      next("/"); // Redirect non-admins
    }
  } else {
    next();
  }
});
```

## Step 4: Ensure API URL is Configured

In your `.env.local` file:

```
VUE_APP_API_URL=http://localhost:8099
```

Or update `ProviderConfigChatbot.vue` directly:

```javascript
const API_BASE = "http://localhost:8099";
```

## Step 5: Test It!

1. Click "Admin Panel" in your sidebar
2. The Admin Panel should load with tabs
3. Click "Provider Configuration" tab
4. You should see the provider selection screen

## Visual Flow

```
Sidebar Click "Admin Panel"
         ↓
Router navigates to /admin
         ↓
AdminPanel.vue loads
         ↓
ProviderConfigChatbot appears in first tab
         ↓
User configures provider via chatbot
```

## File Placement

```
frontend/
├── components/
│   ├── AdminPanel.vue              ← New
│   ├── ProviderConfigChatbot.vue   ← Already created
│   ├── Sidebar.vue                 ← Update this
│   └── ... other components
├── router.ts                        ← Update this
├── App.vue
└── main.ts
```

## Common Issues & Solutions

### Issue: "Cannot GET /admin"

**Solution:** Check your router configuration. The route must be defined in router.ts

### Issue: ProviderConfigChatbot not loading

**Solution:**

- Check that both Vue files are in the same `components` directory
- Verify import path: `import ProviderConfigChatbot from "./ProviderConfigChatbot.vue";`

### Issue: API calls returning 404

**Solution:**

- Verify backend is running: `http://localhost:8099/health`
- Check `VUE_APP_API_URL` environment variable
- Ensure CORS is enabled in backend

### Issue: "Only admins can access"

**Solution:**

- Update user role in your auth store to "admin"
- Or temporarily remove the `requiresAdmin` check from route meta for testing

## Next Steps

After integrating Admin Panel:

1. ✅ Provider Configuration works
2. ⏳ Implement System Status tab (shows backend health)
3. ⏳ Implement System Logs tab (shows error/debug logs)
4. ⏳ Implement User Management tab (manage admin users)

## Need Help?

- Check `ADMIN_PANEL_INTEGRATION.md` for detailed documentation
- Check backend logs: `logs/backend/backend_server.log`
- Browser console for frontend errors (F12)
