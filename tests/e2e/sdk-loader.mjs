// Позволяет запустить код из miniapp/tgcloud в обычном Node: импорт 'sdk' → тестовый шим.
import { register } from 'node:module';
register('data:text/javascript,' + encodeURIComponent(`
  export async function resolve(spec, ctx, next) {
    if (spec === 'sdk') return { url: ${JSON.stringify(new URL('./sdk-shim.mjs', import.meta.url).href)}, shortCircuit: true };
    return next(spec, ctx);
  }`));
