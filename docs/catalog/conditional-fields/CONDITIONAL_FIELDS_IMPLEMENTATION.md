# Conditional Fields Implementation Guide

## Overview

The backend now captures conditional field dependencies from ServiceNow. Fields can be shown/hidden based on the values of other fields (e.g., "What was the original phone number?" should only appear when "Is this a replacement?" is set to "Yes").

## Backend Changes

The API now returns conditional field metadata with each catalog form:

```json
{
  "sys_id": "variable_sys_id",
  "name": "variable_name",
  "label": "What was the original phone number?",
  "type": "text",
  "mandatory": false,
  "depends_on": "original_variable_sys_id",
  "inactive_on_value": "No",
  "choices": []
}
```

## How It Works

1. **`depends_on`** - The sys_id of the parent field that controls visibility
2. **`inactive_on_value`** - The value of the parent field that makes this field invisible

### Example

When "Is this a replacement for a lost or broken iPhone?" is set to:
- **"Yes"** → Show "What was the original phone number?" field
- **"No"** → Hide "What was the original phone number?" field

## Frontend Implementation Steps

### 1. Build Dependency Map

```javascript
const buildDependencyMap = (variables) => {
  const deps = {};
  variables.forEach((variable) => {
    if (variable.depends_on && variable.inactive_on_value) {
      deps[variable.sys_id] = {
        parentFieldSysId: variable.depends_on,
        hideOnValue: variable.inactive_on_value,
      };
    }
  });
  return deps;
};
```

### 2. Check Field Visibility

```javascript
const isFieldVisible = (fieldSysId, formValues, dependencies) => {
  const dependency = dependencies[fieldSysId];
  if (!dependency) return true; // No dependency, always visible
  
  const parentValue = formValues[dependency.parentFieldSysId];
  return parentValue !== dependency.hideOnValue;
};
```

### 3. Conditional Rendering

```javascript
{isFieldVisible(variable.sys_id, formValues, dependencies) && (
  <FormField variable={variable} />
)}
```

### 4. Validation

Only validate mandatory fields that are visible:

```javascript
const validateForm = (formValues, variables, dependencies) => {
  const errors = {};
  
  variables.forEach((variable) => {
    if (isFieldVisible(variable.sys_id, formValues, dependencies)) {
      if (variable.mandatory && !formValues[variable.sys_id]) {
        errors[variable.sys_id] = `${variable.label} is required`;
      }
    }
  });
  
  return errors;
};
```

### 5. Form Submission

Remove hidden field values before submitting:

```javascript
const cleanFormData = (formValues, variables, dependencies) => {
  const cleaned = {};
  
  variables.forEach((variable) => {
    if (isFieldVisible(variable.sys_id, formValues, dependencies)) {
      cleaned[variable.sys_id] = formValues[variable.sys_id];
    }
  });
  
  return cleaned;
};
```

## Test Cases

1. Load form → Check that conditional fields have `depends_on` and `inactive_on_value`
2. Set parent field to "Yes" → Dependent field appears
3. Set parent field to "No" → Dependent field disappears
4. Fill hidden field → Clear on visibility change
5. Submit form → Only visible fields are sent

## API Response Example

```bash
GET /api/catalog/{sys_id}/form

Response:
{
  "item": {...},
  "variables": [
    {
      "sys_id": "q1_sys_id",
      "label": "Is this a replacement for a lost or broken iPhone?",
      "type": "select",
      "choices": [
        {"label": "Yes", "value": "Yes"},
        {"label": "No", "value": "No"}
      ]
    },
    {
      "sys_id": "q2_sys_id",
      "label": "What was the original phone number?",
      "type": "text",
      "depends_on": "q1_sys_id",        // Links to parent field
      "inactive_on_value": "No"         // Hidden when parent = "No"
    }
  ]
}
```

## Summary

✓ Backend now fetches `depends_on` and `inactive_on_value` from ServiceNow
✓ API returns conditional metadata in every catalog form
✓ Frontend should implement conditional rendering based on these fields
✓ Validation should only check visible fields
✓ Form submission should only include visible field values

The business logic is now fully captured - implement the conditional rendering in your frontend component!