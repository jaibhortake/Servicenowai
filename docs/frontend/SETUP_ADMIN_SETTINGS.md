# Setup Admin Settings Panel - Step by Step

## Files Already Created

✅ `src/components/AdminPanel.vue` - Main admin panel component  
✅ `src/components/ProviderConfigChatbot.vue` - Provider configuration chatbot  
✅ `src/pages/AdminPage.vue` - Admin page wrapper

## What You Need to Do

### Step 1: Update Your Router

Find your router file (likely `src/router/index.ts` or `src/router.ts`) and add this route:

```typescript
import AdminPage from "@/pages/AdminPage.vue";

const routes = [
  // ... your existing routes ...

  {
    path: "/admin",
    component: AdminPage,
    name: "admin",
    meta: {
      title: "Admin Panel",
      requiresAuth: true, // Optional: only if you have auth
    },
  },
];
```

### Step 2: Update Sidebar/Navigation

Find where your "Admin Panel" link is and update it to use the router:

**Before:**

```vue
<div class="menu-item">
  <span>Admin Panel</span>
</div>
```

**After:**

```vue
<router-link to="/admin" class="menu-item">
  <span>⚙️ Admin Panel</span>
</router-link>
```

Or if using a different navigation structure:

```vue
<button @click="$router.push('/admin')" class="menu-item">
  Admin Panel
</button>
```

### Step 3: Set Environment Variable

Create or update `.env` file in your frontend root directory:

```
VITE_API_URL=http://localhost:8099
```

Or if using `.env.local`:

```
VUE_APP_API_URL=http://localhost:8099
```

### Step 4: Update ProviderConfigChatbot (if using Vite)

If your frontend uses Vite, update `ProviderConfigChatbot.vue` line with API URL:

```javascript
// Change this line to use environment variable
const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8099";
```

Or keep the hardcoded URL if it's always on localhost:8099.

### Step 5: Restart Frontend Dev Server

Kill the current frontend server and restart it:

```bash
# If using npm
npm run dev

# If using yarn
yarn dev

# If using pnpm
pnpm dev
```

Or on Windows:

```powershell
# Kill existing process
Get-Process node -ErrorAction SilentlyContinue | Stop-Process -Force

# Restart
cd D:\GraphAI_ServiceNow\frontend
npm run dev
```

### Step 6: Test It!

1. Go to `http://localhost:5200` in your browser
2. Click on "Admin Panel" in the sidebar
3. You should see the Admin Panel with tabs
4. Click "Provider Configuration" tab
5. Select a provider to configure

## Troubleshooting

### Issue: "Cannot find module 'AdminPanel'"

**Fix:** Ensure both component files are in `src/components/`:

- `src/components/AdminPanel.vue`
- `src/components/ProviderConfigChatbot.vue`

### Issue: "Error importing module"

**Fix:** Check import paths in `AdminPage.vue`:

```vue
import AdminPanel from "@/components/AdminPanel.vue";
```

### Issue: Page loads but provider configuration doesn't show

**Fix:** Check browser console (F12). Likely API URL issue:

- Ensure backend is running on http://localhost:8099
- Check `VITE_API_URL` or `VUE_APP_API_URL` is set correctly
- Check CORS is enabled in backend

### Issue: "Provider list won't load"

**Fix:**

1. Open browser console (F12)
2. Look for network errors
3. Check if `http://localhost:8099/api/settings/providers` responds
4. Update API_BASE in `ProviderConfigChatbot.vue` if needed

## Complete File Locations

```
frontend/
├── src/
│   ├── components/
│   │   ├── AdminPanel.vue                    ✅
│   │   └── ProviderConfigChatbot.vue         ✅
│   ├── pages/
│   │   └── AdminPage.vue                     ✅
│   ├── router/
│   │   └── index.ts or router.ts             📝 UPDATE THIS
│   └── App.vue (contains navbar/sidebar)     📝 UPDATE THIS
├── .env or .env.local                        📝 ADD THIS
└── package.json

backend/
├── app/
│   ├── providers/
│   │   ├── provider_config.py
│   │   ├── provider_registry.py
│   │   └── provider_templates.json
│   ├── routers/
│   │   └── settings.py
│   └── main.py                               ✅ UPDATED
└── (running on port 8000)                    ✅ RUNNING
```

## Example Router Configuration

Here's a complete example if you need it:

```typescript
// src/router/index.ts
import { createRouter, createWebHistory } from "vue-router";
import AdminPage from "@/pages/AdminPage.vue";
import NewServiceRequest from "@/pages/NewServiceRequest.vue";
import MyTickets from "@/pages/MyTickets.vue";

const routes = [
  {
    path: "/",
    component: NewServiceRequest,
    name: "home",
  },
  {
    path: "/tickets",
    component: MyTickets,
    name: "tickets",
  },
  {
    path: "/admin",
    component: AdminPage,
    name: "admin",
    meta: { title: "Admin Panel" },
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

export default router;
```

## Next Steps

Once Admin Panel is working:

1. ✅ Configure your first provider (ServiceNow, Jira, etc.)
2. ⏳ Frontend will use configured provider automatically
3. ⏳ You can add more providers or switch providers anytime

## Still Having Issues?

Check these files in order:

1. Browser console for errors (F12)
2. Backend logs: `logs/backend/backend_server.log`
3. Frontend dev server console output
4. Network tab to see API requests

Need more help? Check:

- `QUICK_START_ADMIN_PANEL.md`
- `PROVIDER_CONFIGURATION.md`
