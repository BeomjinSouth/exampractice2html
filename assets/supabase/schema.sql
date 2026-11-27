-- 학생 기록 저장소. Supabase SQL Editor에서 전체를 실행하세요.
-- 교사 계정은 Authentication에서 만든 뒤 아래 예시의 이메일을 바꿔 실행합니다.
-- insert into public.sheet_teachers (email) values ('teacher@example.com') on conflict do nothing;
-- 공개 회원가입은 끄고, 교사 이메일과 계정 관리에 주의하세요.

create table if not exists public.sheet_teachers (
  email text primary key check (char_length(email) between 3 and 320)
);

create table if not exists public.sheet_records (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  sheet_id text not null check (char_length(sheet_id) between 1 and 32),
  sheet_title text not null check (char_length(sheet_title) between 1 and 200),
  build text not null check (char_length(build) between 1 and 64),
  client_id text not null check (char_length(client_id) between 1 and 128),
  student jsonb not null check (jsonb_typeof(student) = 'object' and octet_length(student::text) <= 4096),
  event text not null check (event in ('check', 'submit')),
  item text check (item is null or char_length(item) <= 128),
  score jsonb not null check (jsonb_typeof(score) = 'object' and octet_length(score::text) <= 8192),
  answers jsonb not null check (jsonb_typeof(answers) = 'object' and octet_length(answers::text) <= 262144),
  verdicts jsonb not null check (jsonb_typeof(verdicts) = 'object' and octet_length(verdicts::text) <= 65536)
);

create index if not exists sheet_records_sheet_created_idx
  on public.sheet_records (sheet_id, created_at desc);

alter table public.sheet_teachers enable row level security;
alter table public.sheet_records enable row level security;

-- 자신의 이메일만 조회할 수 있으므로 교사 명단 전체는 공개되지 않습니다.
revoke all on public.sheet_teachers from public, anon, authenticated;
revoke all on public.sheet_records from public, anon, authenticated;
grant select (email) on public.sheet_teachers to authenticated;
grant insert (sheet_id, sheet_title, build, client_id, student, event, item, score, answers, verdicts)
  on public.sheet_records to anon;
grant select on public.sheet_records to authenticated;

drop policy if exists sheet_teachers_self on public.sheet_teachers;
create policy sheet_teachers_self on public.sheet_teachers for select to authenticated
  using (email = (select auth.jwt() ->> 'email'));

drop policy if exists sheet_records_insert on public.sheet_records;
create policy sheet_records_insert on public.sheet_records for insert to anon
  with check (true);

drop policy if exists sheet_records_teacher_select on public.sheet_records;
create policy sheet_records_teacher_select on public.sheet_records for select to authenticated
  using (exists (select 1 from public.sheet_teachers where email = (select auth.jwt() ->> 'email')));
