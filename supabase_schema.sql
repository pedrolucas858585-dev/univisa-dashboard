-- ============================================================
-- UNIVISA Dashboard — Schema Supabase
-- Execute no SQL Editor do Supabase (dashboard.supabase.com)
-- ============================================================

-- Tabela de usuários
CREATE TABLE IF NOT EXISTS public.users (
  id         BIGSERIAL PRIMARY KEY,
  login      TEXT UNIQUE NOT NULL,
  senha_hash TEXT NOT NULL,
  nome       TEXT,
  role       TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin','user')),
  criado_em  TIMESTAMPTZ DEFAULT NOW()
);

-- Tabela de uploads de planilhas
CREATE TABLE IF NOT EXISTS public.uploads (
  id            BIGSERIAL PRIMARY KEY,
  nome_arquivo  TEXT NOT NULL,
  ano           TEXT,
  dados         JSONB NOT NULL,
  usuario_id    BIGINT REFERENCES public.users(id) ON DELETE SET NULL,
  criado_em     TIMESTAMPTZ DEFAULT NOW()
);

-- ── Row Level Security ─────────────────────────────────────
ALTER TABLE public.users  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.uploads ENABLE ROW LEVEL SECURITY;

-- Políticas: acesso total via service_role (usado pelo app)
-- (O app usa a service key no backend, não expõe ao browser)

CREATE POLICY "service_full_users"
  ON public.users
  USING (true)
  WITH CHECK (true);

CREATE POLICY "service_full_uploads"
  ON public.uploads
  USING (true)
  WITH CHECK (true);

-- ── Usuário admin padrão ───────────────────────────────────
-- Senha: admin123  (SHA-256 abaixo)
-- Para mudar: gere o hash em https://emn178.github.io/online-tools/sha256.html
INSERT INTO public.users (login, senha_hash, nome, role)
VALUES (
  'admin',
  '240be518fabd2724ddb6f04eeb1da5967448d7e831c08c8fa822809f74c720a9',
  'Administrador',
  'admin'
)
ON CONFLICT (login) DO NOTHING;
