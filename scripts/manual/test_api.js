// Test script to check API responses
const API_URL = process.env.APP_FRONTEND_API_URL || process.env.VITE_API_URL;

if (!API_URL) {
  throw new Error('APP_FRONTEND_API_URL or VITE_API_URL must be configured');
}

const apiUrl = (path) => `${API_URL.replace(/\/$/, '')}${path}`;

async function testAPI() {
  console.log('Testing ServiceNow AI Portal API...\n');

  try {
    // Test 1: Get health
    console.log('1. Checking backend health...');
    const health = await fetch(apiUrl('/health'));
    const healthData = await health.json();
    console.log('✓ Backend:', healthData.status === 'ok' ? 'OK' : 'ERROR');

    // Test 2: Get catalog items
    console.log('\n2. Getting catalog items...');
    const catalogResponse = await fetch(apiUrl('/api/catalog?limit=1'));
    const catalogItems = await catalogResponse.json();
    if (catalogItems.length > 0) {
      console.log('✓ Found catalog items:', catalogItems.length);
      const item = catalogItems[0];
      console.log('  First item:', item.name);

      // Test 3: Get catalog form with variables
      console.log('\n3. Getting catalog form...');
      const formResponse = await fetch(apiUrl(`/api/catalog/${item.sys_id}/form`));
      const formData = await formResponse.json();
      console.log('✓ Form loaded');
      console.log('  Variables:', formData.variables.length);

      // Check for depends_on field
      const withDeps = formData.variables.filter(v => v.depends_on);
      console.log('  Fields with dependencies:', withDeps.length);

      if (withDeps.length > 0) {
        console.log('\n  Dependency example:');
        const dep = withDeps[0];
        console.log('    Field:', dep.label);
        console.log('    Depends on:', dep.depends_on);
        console.log('    Hidden when:', dep.inactive_on_value);
      }

      // Show all variables
      console.log('\n4. All variables:');
      formData.variables.forEach((v, i) => {
        const deps = v.depends_on ? ` [depends on: ${v.depends_on}, inactive_on: ${v.inactive_on_value}]` : '';
        console.log(`  ${i+1}. ${v.label}${deps}`);
      });
    } else {
      console.log('✗ No catalog items found');
    }
  } catch (error) {
    console.error('❌ Error:', error.message);
  }
}

testAPI();
