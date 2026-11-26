-- O esquema do banco do projeto, no PostgreSQL 16 da UNIGEO, com o PostGIS 3.4.
--
-- Os nomes não levam o schema na frente: `modulos/banco.py` conecta apontando para o schema certo
-- (o `climageo`, ou o `climageo_teste` nos testes), e o mesmo arquivo serve aos dois. Pode rodar de
-- novo sem medo: tudo é "IF NOT EXISTS" ou "OR REPLACE".
--
-- O banco guarda só as horas (decidido em 09/10/2026): o dia, a semana e o mês saem delas na hora
-- de ler, pela regra do Python que o painel e os produtos usam. O EC46 é a exceção, porque já
-- chega por semana, e a chuva do CEMADEN fica por leitura (de 10 em 10 minutos, chovendo), porque é
-- assim que a PED manda. As decisões estão em docs/escopo_v0.3.1.md (decisão 6) e no plano, em
-- docs/plano_arquitetura.md (passo 3).
--
-- As medidas são `real` (4 bytes), e voltam exatas: o PostgreSQL escreve o `real` pela representação
-- mais curta (889.2, e não 889.2000122), o psycopg o lê em texto, e todo número de até 6 algarismos
-- faz a volta sem perda (é a garantia do `real`; as medidas do INMET e do Open-Meteo têm até 5).
-- Conferido em 09/10/2026: uma rodada da grade e um dia das estações de MS voltaram iguais, bit a
-- bit, ao que as APIs mandam. As coordenadas são a exceção, e vão em `double precision`: as das
-- estações vêm da lista do INMET com 8 casas (-15.78944444).
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
-- OS PLUVIÔMETROS DO CEMADEN
-- =====================================================
-- A rede do CEMADEN (a PED) é outra que a do INMET, com outros códigos e outro cadastro, e fica em
-- tabelas à parte. A chuva vai como a PED manda, uma linha por leitura: sem chuva, uma por hora, com
-- o zero escrito, num minuto que é de cada estação; chovendo, de 10 em 10 minutos. A hora e o dia
-- saem delas na leitura, como as do INMET, e a hora sem leitura nenhuma é hora sem dado (a estação
-- parada não manda nada). Conferido contra a PED em 03/11/2026.
CREATE TABLE IF NOT EXISTS cemaden_estacao (
    codigo         text PRIMARY KEY,
    id_estacao     integer,
    nome           text NOT NULL,
    municipio      text,
    codibge        integer,
    uf             char(2) NOT NULL,
    latitude       double precision NOT NULL,
    longitude      double precision NOT NULL,
    tipo           text,
    instalada_em   timestamp,
    ultima_parada  timestamp,
    ultima_remessa timestamp,
    atualizada_em  timestamptz NOT NULL DEFAULT now(),
    geom           geometry(Point, 4326)
                   GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)) STORED
);
COMMENT ON TABLE cemaden_estacao IS
    'O cadastro das estações do CEMADEN no estado, como a PED manda, as ativas e as paradas.';
COMMENT ON COLUMN cemaden_estacao.codigo IS 'O código da estação no CEMADEN (ex.: 500270402A).';
COMMENT ON COLUMN cemaden_estacao.tipo IS 'Como a PED diz: "Pluviométrica" ou "Hidrológica".';
COMMENT ON COLUMN cemaden_estacao.ultima_parada IS
    'O começo da última parada da estação, como a PED diz, sem fuso declarado. Toda estação tem: a que '
    'mandou dado depois (ultima_remessa mais nova) voltou.';
COMMENT ON COLUMN cemaden_estacao.ultima_remessa IS 'A última remessa de dados, como a PED diz, sem fuso declarado.';

CREATE TABLE IF NOT EXISTS cemaden_chuva (
    estacao     text NOT NULL REFERENCES cemaden_estacao (codigo),
    leitura_utc timestamptz NOT NULL,
    chuva       real,
    coletada_em timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (estacao, leitura_utc)
) PARTITION BY RANGE (leitura_utc);
CREATE INDEX IF NOT EXISTS cemaden_chuva_leitura ON cemaden_chuva (leitura_utc);
COMMENT ON TABLE cemaden_chuva IS
    'As leituras de chuva das estações do CEMADEN, uma linha por leitura, como a PED manda. Uma '
    'partição por mês, para sempre.';
COMMENT ON COLUMN cemaden_chuva.leitura_utc IS 'O momento da leitura, em UTC, como a estação mandou (ex.: 13:40).';
COMMENT ON COLUMN cemaden_chuva.chuva IS
    'mm desde a leitura anterior da estação (conferido: a soma do dia bate com o acumulado de 24 h da PED).';

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
    latitude          double precision NOT NULL,
    longitude         double precision NOT NULL,
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

-- As colunas uma a uma, e não *: quem usa o banco pode acrescentar coluna numa tabela só (a UNIGEO
-- pôs uma objectid na das estações, para o ArcGIS, visto em 05/11/2026), e o * desencontraria o UNION
CREATE OR REPLACE VIEW previsao_horaria AS
    SELECT 'grade'::text AS conjunto, modelo, rodada_utc, ponto, latitude, longitude, hora_prevista_utc,
           temperatura, umidade, orvalho, chuva, vento, rajada, direcao
    FROM previsao_horaria_grade
    UNION ALL
    SELECT 'estacao'::text AS conjunto, modelo, rodada_utc, ponto, latitude, longitude, hora_prevista_utc,
           temperatura, umidade, orvalho, chuva, vento, rajada, direcao
    FROM previsao_horaria_estacao;
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
    latitude         double precision NOT NULL,
    longitude        double precision NOT NULL,
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

-- =====================================================
-- OS RAIOS DO GOES (GLM), POR HORA E CÉLULA
-- =====================================================
-- Os flashes vêm das tabelas que um colega da unidade alimenta no mesmo banco (o schema
-- eventos_atmosfericos_rt): um flash por linha, do disco inteiro do GOES-19, sem índice no horário.
-- Ler um mês dali levaria minutos. Aqui fica só a contagem da caixa do estado com ~1° de folga, por
-- hora e por célula de 0,1°, que o coletor (`python coletar.py raios`) refaz a cada 10 minutos para
-- as horas que ganharam arquivo novo. O flash entra na hora do arquivo de 20 s que o trouxe: o que
-- começou nos últimos segundos de uma hora e terminou na seguinte vai para a seguinte. Sem partições,
-- para o coletor não precisar criar tabela no meio do caminho: de 2018 até hoje são poucos GB.
CREATE TABLE IF NOT EXISTS raios_glm_hora (
    hora_utc   timestamptz NOT NULL,
    lon_celula smallint NOT NULL,
    lat_celula smallint NOT NULL,
    flashes    integer NOT NULL,
    PRIMARY KEY (hora_utc, lon_celula, lat_celula)
);
COMMENT ON TABLE raios_glm_hora IS
    'Os flashes do GLM do GOES-19 na caixa do estado com ~1° de folga, contados por hora e por célula '
    'de 0,1°. A célula sem flash não tem linha. Vem das tabelas de eventos_atmosfericos_rt.';
COMMENT ON COLUMN raios_glm_hora.hora_utc IS
    'O fim da hora, em UTC, como na chuva: a das 14:00 tem os arquivos de 13:00 a 13:59:40.';
COMMENT ON COLUMN raios_glm_hora.lon_celula IS
    'A célula em décimos de grau: floor(longitude × 10). A -546 vai de -54,6° a -54,5°.';
COMMENT ON COLUMN raios_glm_hora.lat_celula IS
    'A célula em décimos de grau: floor(latitude × 10). A -205 vai de -20,5° a -20,4°.';

CREATE TABLE IF NOT EXISTS raios_glm_cobertura (
    hora_utc    timestamptz PRIMARY KEY,
    arquivos    smallint NOT NULL,
    sem_arquivo smallint NOT NULL,
    flashes     integer NOT NULL,
    contada_em  timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE raios_glm_cobertura IS
    'Quantos dos 180 arquivos de 20 s de cada hora já estavam na contagem: separa a hora sem raio da '
    'hora que ainda não foi processada. Uma linha por hora que tem algum arquivo no controle.';
COMMENT ON COLUMN raios_glm_cobertura.arquivos IS 'Os arquivos processados (status CONCLUIDO no controle).';
COMMENT ON COLUMN raios_glm_cobertura.sem_arquivo IS
    'Os arquivos que a NOAA não publicou (status ENCERRADO_SEM_CENA): a hora está completa quando '
    'arquivos + sem_arquivo = 180.';
COMMENT ON COLUMN raios_glm_cobertura.flashes IS 'O total de flashes da hora na caixa.';

CREATE TABLE IF NOT EXISTS raios_glm_coleta (
    id                 smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    ultimo_id_controle bigint NOT NULL,
    atualizada_em      timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE raios_glm_coleta IS
    'Até onde o coletor dos raios leu o controle do GLM: o maior id que ele já viu. Uma linha só.';

-- =====================================================
-- OS FLASHES DO GLM ATÉ 2.000 KM DE CAMPO GRANDE
-- =====================================================
-- A nossa cópia das tabelas do colega (eventos_atmosfericos_rt), pedida em 26/11/2026: a dele guarda
-- o disco inteiro do GOES-19 (~1 GB por dia), com uma coluna jsonb que repete as outras e pesa 74%
-- da linha. Aqui ficam só os flashes a até config.RAIO_DA_TABELA_GLM_KM de Campo Grande (~55% deles,
-- medido em 26/11/2026), sem o jsonb. As colunas, os nomes e a ligação com o controle (id_cena é o id
-- do arquivo de 20 s) são os da dele, para as consultas dos raios valerem nas duas. O que já estava
-- lá entra com os mesmos ids, por ferramentas/recorte_glm.py; o que o nosso coletor gravar, a partir
-- de 1 bilhão.
CREATE TABLE IF NOT EXISTS controle_glm_rt (
    id                   bigint GENERATED BY DEFAULT AS IDENTITY (START WITH 1000000000) PRIMARY KEY,
    data_hora_referencia timestamptz NOT NULL UNIQUE,
    source_key           text UNIQUE,
    status               text NOT NULL DEFAULT 'PENDENTE'
                         CHECK (status IN ('PENDENTE', 'CONCLUIDO', 'ENCERRADO_SEM_CENA')),
    tentativas           integer NOT NULL DEFAULT 0 CHECK (tentativas >= 0),
    concluido_em         timestamptz,
    mensagem_erro        text
);
COMMENT ON TABLE controle_glm_rt IS
    'Os arquivos de 20 s do GLM (GLM-L2-LCFA do GOES-19, no bucket público da NOAA) que alimentam a '
    'glm_rt, um por linha. Mesmas colunas do controle_processamento_glm do colega.';
COMMENT ON COLUMN controle_glm_rt.data_hora_referencia IS 'O começo do arquivo (o "s" do nome), em UTC.';
COMMENT ON COLUMN controle_glm_rt.source_key IS 'O caminho do arquivo no bucket.';
COMMENT ON COLUMN controle_glm_rt.status IS
    'CONCLUIDO: os flashes estão na glm_rt; ENCERRADO_SEM_CENA: a NOAA não publicou o arquivo; PENDENTE: falta.';

-- Os índices são os das leituras: pelo arquivo (id_cena, a rota dos raios, que passa pelo controle) e
-- pelo horário do flash. Sem índice espacial: as consultas do projeto filtram a longitude e a
-- latitude depois do horário. A geom sai da longitude e da latitude, como nas estações
CREATE TABLE IF NOT EXISTS glm_rt (
    id_registro                            bigint GENERATED BY DEFAULT AS IDENTITY (START WITH 1000000000)
                                           PRIMARY KEY,
    id_cena                                bigint NOT NULL REFERENCES controle_glm_rt (id),
    flash_id                               bigint,
    time_utc                               timestamptz,
    flash_time_offset_of_first_event       double precision,
    flash_time_offset_of_last_event        double precision,
    flash_frame_time_offset_of_first_event double precision,
    flash_frame_time_offset_of_last_event  double precision,
    flash_lat                              double precision,
    flash_lon                              double precision,
    flash_area                             double precision,
    flash_energy                           double precision,
    flash_quality_flag                     integer,
    geom                                   geometry(Point, 4326)
                                           GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(flash_lon, flash_lat), 4326)) STORED
);
CREATE INDEX IF NOT EXISTS glm_rt_cena ON glm_rt (id_cena);
CREATE INDEX IF NOT EXISTS glm_rt_hora ON glm_rt (time_utc);
COMMENT ON TABLE glm_rt IS
    'Os flashes do GLM do GOES-19 a até 2.000 km de Campo Grande, um por linha. Colunas da '
    'relampagos_glm_rt do colega, sem a atributos_originais.';
COMMENT ON COLUMN glm_rt.id_cena IS 'O arquivo de 20 s que trouxe o flash (controle_glm_rt.id).';
COMMENT ON COLUMN glm_rt.time_utc IS
    'O começo do flash, em UTC: o começo do arquivo mais flash_time_offset_of_first_event.';
COMMENT ON COLUMN glm_rt.flash_area IS 'm².';
COMMENT ON COLUMN glm_rt.flash_energy IS 'J.';
COMMENT ON COLUMN glm_rt.flash_quality_flag IS '0: sem aviso; os outros (3 e 5, vistos em 2026) são avisos de qualidade do produto da NOAA.';
