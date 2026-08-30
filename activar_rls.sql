-- ============================================================
-- ACTIVAR RLS EN TODAS LAS TABLAS DEL ESQUEMA 'public'
-- Supabase > SQL Editor > New query > pegar y Run
-- ============================================================

-- 1) Activar Row Level Security en TODAS las tablas de 'public'
DO $$
DECLARE
    tbl record;
BEGIN
    FOR tbl IN
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY;', tbl.tablename);
        RAISE NOTICE 'RLS activado en: public.%', tbl.tablename;
    END LOOP;
END $$;

-- 2) Verificar que quedó activado (rowsecurity = true)
SELECT tablename, rowsecurity
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;

-- ============================================================
-- AVISOS IMPORTANTES
-- ============================================================
-- a) Con RLS activado y SIN políticas, los roles 'anon' y 'authenticated'
--    (la API de Supabase / PostgREST) NO ven ninguna fila (deny por defecto).
--    Si algún día usas la API, tendrás que crear políticas por tabla, ej.:
--
--    CREATE POLICY "lectura autenticada" ON public.clientes
--      FOR SELECT TO authenticated USING (true);
--
-- b) Tu app (conexion.py) se conecta con el rol 'postgres' (superusuario).
--    Los superusuarios y el dueño de la tabla SIEMPRE omiten RLS,
--    incluso con FORCE ROW LEVEL SECURITY.
--    => Activar RLS NO rompe tu app, pero TAMPOCO la protege.
--
-- c) Las tablas nuevas que crea la app (CREATE TABLE IF NOT EXISTS)
--    NO tendrán RLS. Hay que re-ejecutar este script o agregar
--    'ENABLE ROW LEVEL SECURITY' a cada CREATE TABLE.
-- ============================================================
