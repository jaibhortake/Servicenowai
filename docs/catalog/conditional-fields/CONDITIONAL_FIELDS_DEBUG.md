# Conditional Fields Debugging Guide

## Problem

All form fields are visible, but some should only appear when you select "Yes" or "No" on a selection field.

## Debugging Steps

### Step 1: Open Browser Console

1. Open http://localhost:5200
2. Search for "Apple iPhone 13 Pro" (or the catalog item with conditional fields)
3. Open the form
4. Press **F12** (or Ctrl+Shift+I) to open Developer Tools
5. Go to **Console** tab

### Step 2: Check What the API Returns

Look for messages like:

```
[FORM] Conditional fields found:
  - "Field Name 1": depends_on="parent_sys_id", inactive_on_value="No"
  - "Field Name 2": depends_on="parent_sys_id", inactive_on_value="Yes"
```

**If you see:**

- ✅ `[FORM] Conditional fields found:` → The API is returning conditional metadata
- ❌ `[FORM] No conditional fields in this form` → **Problem 1**: ServiceNow doesn't have conditional fields configured

### Step 3: Check Field Visibility Status

Look for messages like:

```
[FORM_STATUS] Showing 8 of 12 fields
[HIDDEN_FIELDS] "Field A", "Field B", "Field C"
```

**If you see:**

- ✅ `Showing X of Y fields` (where X < Y) → Conditional logic is working
- ❌ `Showing 12 of 12 fields` → **Problem 2**: No fields are being hidden

### Step 4: Change a Field Value

1. Find the field that controls visibility (usually a "Yes/No" or selection field)
2. Change its value
3. Watch the console for:

```
[FIELD_CHANGE] "Parent Field" changed to "Yes"
[CONDITIONAL] "Child Field": depends_on="Parent Field", parentValue="Yes", inactive_on_value="No", visible=true
```

**This tells you:**

- `parentValue="Yes"` - What the user selected
- `inactive_on_value="No"` - When the field should be hidden
- `visible=true/false` - Whether field is actually visible

### Step 5: Identify the Issue

#### Issue 1: No Conditional Fields in API Response

```
[FORM] No conditional fields in this form
```

**Cause:** ServiceNow catalog item doesn't have conditional field rules configured
**Solution:** Check ServiceNow to add conditional field dependencies

#### Issue 2: depends_on Reference is Wrong

```
[CONDITIONAL] "Parent Field not found: depends_on="unknown_sys_id"
```

**Cause:** The `depends_on` value doesn't match any field in the form
**Solution:** Check ServiceNow - the reference is pointing to wrong field

#### Issue 3: inactive_on_value Doesn't Match User Selection

```
[CONDITIONAL] "Child Field": parentValue="Yes", inactive_on_value="No", visible=true
```

**But you expect:** Field should be hidden when parentValue="Yes"
**Cause:** The value comparison doesn't match
**Solution:** Check ServiceNow - the `inactive_on_value` should be "Yes" not "No"

#### Issue 4: All Fields Visible Despite Conditional Logic

```
[FORM_STATUS] Showing 12 of 12 fields
[CONDITIONAL] "Child Field": visible=false
```

**But fields still show visually**
**Cause:** Field is filtered but still rendering
**Solution:** Check if there's a caching issue - reload page (Ctrl+F5)

## What Each Log Message Means

### [FORM] Messages

```
[FORM] Conditional fields found:
  - "What was the original phone number?": depends_on="e2a3714a...", inactive_on_value="No"
```

- This field should hide when parent field value = "No"
- Parent field ID is `e2a3714a...`

### [CONDITIONAL] Messages

```
[CONDITIONAL] "What was the original phone number?": depends_on="Is this a replacement?", parentValue="Yes", inactive_on_value="No", visible=true
```

- Field: "What was the original phone number?"
- Depends on: "Is this a replacement?"
- Parent current value: "Yes"
- Should be hidden when parent = "No"
- **Status: VISIBLE** ✓ Correct!

vs.

```
[CONDITIONAL] "What was the original phone number?": depends_on="Is this a replacement?", parentValue="No", inactive_on_value="No", visible=false
```

- Parent current value: "No"
- Should be hidden when parent = "No"
- **Status: HIDDEN** ✓ Correct!

### [FIELD_CHANGE] Messages

```
[FIELD_CHANGE] "Is this a replacement?" changed to "Yes"
```

- User just selected "Yes"
- This should trigger visibility check for all dependent fields

### [FORM_STATUS] Messages

```
[FORM_STATUS] Showing 5 of 12 fields
[HIDDEN_FIELDS] "Field A", "Field B", "Field C", "Field D", "Field E"
```

- 5 fields are visible
- 7 fields are hidden because of conditional logic

## How to Share Debug Info

Copy and paste the console output and tell me:

1. **Are there any `[FORM] Conditional fields found` messages?**
   - Yes → Show me the output
   - No → ServiceNow doesn't have conditional fields configured

2. **What does `[FORM_STATUS]` show?**
   - Show me the current number of visible/hidden fields

3. **When you change a field value, what do the `[CONDITIONAL]` messages show?**
   - Paste the relevant messages

## Quick Checklist

- [ ] Opened form in browser
- [ ] Opened Developer Console (F12)
- [ ] See `[FORM]` message about conditional fields
- [ ] See `[FORM_STATUS]` about visible/hidden fields
- [ ] Changed a parent field value
- [ ] Watched console for `[FIELD_CHANGE]` and `[CONDITIONAL]` messages
- [ ] Identified which fields should hide but don't
- [ ] Checked if depends_on and inactive_on_value values look correct

## Expected Behavior (Example)

**Form loads:**

```
[FORM] Conditional fields found:
  - "Phone Number": depends_on="e2a3714a...", inactive_on_value="No"
[FORM_STATUS] Showing 11 of 12 fields
[HIDDEN_FIELDS] "Phone Number"
```

→ "Phone Number" is hidden initially

**User selects "Yes" on parent field:**

```
[FIELD_CHANGE] "Is this a replacement?" changed to "Yes"
[CONDITIONAL] "Phone Number": depends_on="Is this a replacement?", parentValue="Yes", inactive_on_value="No", visible=true
```

→ "Phone Number" field should now appear ✓

**User changes to "No":**

```
[FIELD_CHANGE] "Is this a replacement?" changed to "No"
[CONDITIONAL] "Phone Number": depends_on="Is this a replacement?", parentValue="No", inactive_on_value="No", visible=false
[FORM_STATUS] Showing 11 of 12 fields
[HIDDEN_FIELDS] "Phone Number"
```

→ "Phone Number" field disappears again ✓

## Common Issues and Solutions

| Symptom                                         | Cause                                  | Solution                                        |
| ----------------------------------------------- | -------------------------------------- | ----------------------------------------------- |
| All fields visible, none hidden                 | No conditional fields in ServiceNow    | Configure conditional fields in ServiceNow      |
| No `[FORM]` messages                            | Conditional fields not returned by API | Check MCP tool is requesting `depends_on` field |
| `Parent not found` warning                      | depends_on pointing to wrong field     | Check ServiceNow field reference                |
| Field shows when should hide                    | inactive_on_value doesn't match        | Check the exact value in ServiceNow             |
| Conditional logic shows but fields don't update | Browser cache issue                    | Hard refresh: Ctrl+F5 or Cmd+Shift+R            |

---

**Ready to debug? Open the form and check your console!**
