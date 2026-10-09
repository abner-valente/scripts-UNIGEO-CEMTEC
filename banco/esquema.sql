-- O esquema do banco do projeto, no PostgreSQL 16 da UNIGEO, com o PostGIS 3.4.
--
-- Os nomes não levam o schema na frente: `modulos/banco.py` conecta apontando para o schema certo
-- (o `climageo`, ou o `climageo_teste` nos testes), e o mesmo arquivo serve aos dois. Pode rodar de
-- novo sem medo: tudo é "IF NOT EXISTS" ou "OR REPLACE".
--
-- O banco guarda só as horas (decidido em 09/10/2026): o dia, a semana e o mês saem delas na hora
-- de ler, pela regra do Python que o painel e os produtos usam. O EC46 é a exceção, porque já
-- chega por semana. As decisões estão em docs/escopo_v0.3.1.md (decisão 6) e no plano, em
-- docs/plano_arquitetura.md (passo 3).
--
-- As partições não são criadas aqui: quem as cria e apaga é o coletor, por `modulos/banco.py`,
-- porque o banco não tem pg_partman nem pg_cron. Por isso as tabelas têm de ser do usuário que
-- grava: o PostgreSQL só deixa o dono da tabela criar e apagar partições dela.

-- =====================================================
-- AS ESTAÇÕES DO INMET
-- =====================================================
CREATE TABLE IF NOT EXISTS estacao (
    codigo          text PRIMARY KEY,
    nome            text NOT NULL,
    uf              char(2) NOT NULL,
    latitude        double precision NOT NULL,
    longitude       double precision NOT NULL,
    altitude        real,
    situacao        text,
    inicio_operacao timestamptz,
    atualizada_em   timestamptz NOT NULL DEFAULT now(),
    geom            geometry(Point, 4326)
                    GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)) STORED
);
COMMENT ON TABLE estacao IS
    'O cadastro das estações automáticas do INMET, do Brasil inteiro. Vem da lista do INMET e da '
    'consulta por hora, que traz estações que a lista não tem.';
COMMENT ON COLUMN estacao.situacao IS 'Como o INMET diz: "Operante" ou "Pane".';
COMMENT ON COLUMN estacao.geom IS 'Calculada pelo banco a partir da latitude e da longitude (WGS 84).';

-- =====================================================
-- AS LEITURAS DO INMET, HORA A HORA
-- =====================================================
CREATE TABLE IF NOT EXISTS inmet_horaria (
    estacao     text NOT NULL REFERENCES estacao (codigo),
    hora_utc    timestamptz NOT NULL,
    tem_ins     real,
    tem_max     real,
    tem_min     real,
    umd_ins     real,
    umd_max     real,
    umd_min     real,
    pto_ins     real,
    pto_max     real,
    pto_min     real,
    pre_ins     real,
    pre_max     real,
    pre_min     real,
    ven_vel     real,
    ven_raj     real,
    ven_dir     real,
    rad_glo     real,
    chuva       real,
    tem_sen     real,
    ten_bat     real,
    tem_cpu     real,
    coletada_em timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (estacao, hora_utc)
) PARTITION BY RANGE (hora_utc);
CREATE INDEX IF NOT EXISTS inmet_horaria_hora ON inmet_horaria (hora_utc);
COMMENT ON TABLE inmet_horaria IS
    'As leituras horárias das estações do INMET, como a API manda (os mesmos códigos, em minúsculas). '
    'Uma partição por mês, para sempre. O dia, a semana e o mês saem destas horas.';
COMMENT ON COLUMN inmet_horaria.hora_utc IS
    'A hora que a leitura fecha, em UTC: a das 15:00 é a de 14:00 a 15:00, e a das 00:00 fecha o dia anterior.';
COMMENT ON COLUMN inmet_horaria.ven_vel IS 'm/s, como o INMET manda (os produtos e o painel convertem para km/h).';
COMMENT ON COLUMN inmet_horaria.ven_raj IS 'm/s, como o INMET manda (os produtos e o painel convertem para km/h).';
COMMENT ON COLUMN inmet_horaria.rad_glo IS 'kJ/m².';
COMMENT ON COLUMN inmet_horaria.tem_sen IS
    'Sensação térmica. Não vem na consulta por hora: o coletor a busca por estação, uma vez por dia.';
COMMENT ON COLUMN inmet_horaria.ten_bat IS
    'Tensão da bateria (V). Não vem na consulta por hora: o coletor a busca por estação, uma vez por dia.';
COMMENT ON COLUMN inmet_horaria.tem_cpu IS
    'Temperatura do processador. Não vem na consulta por hora: o coletor a busca por estação, uma vez por dia.';

-- =====================================================
-- A PREVISÃO DO OPEN-METEO, HORA A HORA
-- =====================================================
-- A grade e as estações têm as mesmas colunas, mas prazos diferentes (21 dias e para sempre), e a
-- limpeza apaga partições inteiras: numa tabela só, a da grade levaria junto as horas das
-- estações. Os três modelos (ECMWF, GFS e ICON) ficam juntos, com o modelo na chave. Cada rodada
-- é um insert: nada é atualizado.
CREATE TABLE IF NOT EXISTS previsao_horaria_grade (
    modelo            text NOT NULL,
    rodada_utc        timestamptz NOT NULL,
    ponto             text NOT NULL,
    latitude          real NOT NULL,
    longitude         real NOT NULL,
    hora_prevista_utc timestamptz NOT NULL,
    temperatura       real,
    umidade           real,
    orvalho           real,
    chuva             real,
    vento             real,
    rajada            real,
    direcao           real,
    PRIMARY KEY (modelo, rodada_utc, ponto, hora_prevista_utc)
) PARTITION BY RANGE (rodada_utc);
COMMENT ON TABLE previsao_horaria_grade IS
    'A previsão hora a hora nos pontos da grade de MS, por modelo e rodada. Uma partição por dia da '
    'rodada (UTC), apagada depois de 21 dias.';

CREATE TABLE IF NOT EXISTS previsao_horaria_estacao (LIKE previsao_horaria_grade INCLUDING ALL)
    PARTITION BY RANGE (rodada_utc);
COMMENT ON TABLE previsao_horaria_estacao IS
    'A previsão hora a hora no ponto de cada estação de MS (o ponto é o código do INMET), por modelo '
    'e rodada. Uma partição por mês da rodada (UTC), para sempre: é a base do previsto contra o observado.';

CREATE OR REPLACE VIEW previsao_horaria AS
    SELECT 'grade'::text AS conjunto, * FROM previsao_horaria_grade
    UNION ALL
    SELECT 'estacao'::text AS conjunto, * FROM previsao_horaria_estacao;
COMMENT ON VIEW previsao_horaria IS 'As duas tabelas da previsão horária juntas, para consultar.';

-- =====================================================
-- AS SEMANAS DO EC46
-- =====================================================
-- A exceção ao "só as horas": o EC46 chega por semana, com a anomalia calculada contra a normal do
-- modelo, tirada das reprevisões do ECMWF, e isso não se reconstrói a partir de horas. É pequena
-- (uma rodada por dia, cinco ou seis semanas por ponto) e fica para sempre, sem partições.
CREATE TABLE IF NOT EXISTS previsao_semanal (
    modelo           text NOT NULL,
    rodada_utc       timestamptz NOT NULL,
    conjunto         text NOT NULL CHECK (conjunto IN ('grade', 'estacao')),
    ponto            text NOT NULL,
    latitude         real NOT NULL,
    longitude        real NOT NULL,
    semana           date NOT NULL,
    temperatura      real,
    anom_temperatura real,
    temp_max         real,
    anom_temp_max    real,
    temp_min         real,
    anom_temp_min    real,
    chuva            real,
    anom_chuva       real,
    PRIMARY KEY (modelo, rodada_utc, conjunto, ponto, semana)
);
COMMENT ON TABLE previsao_semanal IS
    'O EC46 por semana (de segunda a domingo, UTC), como o Open-Meteo devolve: a previsão (média dos '
    'membros; na chuva, o total da semana) e a anomalia, que é ela menos a normal do modelo.';

-- As colunas de cada tabela da previsão, com as unidades: as mesmas nas duas horárias
COMMENT ON COLUMN previsao_horaria_grade.temperatura IS '°C, a 2 m, na hora cheia.';
COMMENT ON COLUMN previsao_horaria_grade.umidade IS '%, a 2 m, na hora cheia.';
COMMENT ON COLUMN previsao_horaria_grade.chuva IS 'mm, a da hora que termina na hora prevista.';
COMMENT ON COLUMN previsao_horaria_grade.vento IS 'km/h, a 10 m, na hora cheia.';
COMMENT ON COLUMN previsao_horaria_grade.rajada IS 'km/h, a mais forte da hora que termina na hora prevista.';
COMMENT ON COLUMN previsao_horaria_grade.direcao IS 'graus, de onde o vento sopra.';
COMMENT ON COLUMN previsao_horaria_estacao.temperatura IS '°C, a 2 m, na hora cheia.';
COMMENT ON COLUMN previsao_horaria_estacao.umidade IS '%, a 2 m, na hora cheia.';
COMMENT ON COLUMN previsao_horaria_estacao.chuva IS 'mm, a da hora que termina na hora prevista.';
COMMENT ON COLUMN previsao_horaria_estacao.vento IS 'km/h, a 10 m, na hora cheia.';
COMMENT ON COLUMN previsao_horaria_estacao.rajada IS 'km/h, a mais forte da hora que termina na hora prevista.';
COMMENT ON COLUMN previsao_horaria_estacao.direcao IS 'graus, de onde o vento sopra.';
