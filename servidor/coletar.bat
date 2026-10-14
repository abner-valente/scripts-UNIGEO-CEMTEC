@echo off
REM Roda um coletor do banco (coletar.py) no servidor Windows da unidade.
REM
REM Feito para o Agendador de Tarefas do Windows, uma tarefa para cada trabalho:
REM   coletar.bat inmet            de hora em hora, aos 30 minutos (leva ~75 s)
REM   coletar.bat inmet --extras   uma vez por dia, as 03:00 (leva ~10 min)
REM   coletar.bat previsao         de hora em hora, aos 15 minutos (sem rodada nova, segundos; com
REM                                todas novas, ~10 min)
REM As duas do INMET nunca devem rodar juntas: somadas, passariam das 100 consultas por minuto que
REM o INMET aceita de cada endereco. As 03:00, os extras terminam antes da execucao das 03:30.
REM Em todas, "Executar estando o usuario conectado ou nao" e "Nao iniciar uma nova instancia"
REM (o padrao), para uma execucao demorada nao se sobrepor a seguinte. Tambem roda com dois
REM cliques, para testar.
REM
REM A saida vai para logs\coletor_<trabalho>.log (logs\coletor_inmet.log,
REM logs\coletor_inmet_extras.log e logs\coletor_previsao.log), porque pelo Agendador ninguem ve a
REM janela. A carga do historico, que roda uma vez so, e melhor rodar a mao, num terminal:
REM python coletar.py inmet --historico 2018-01-01

setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo Ambiente virtual nao encontrado em %CD%\.venv
    echo Crie com: py -3.14 -m venv .venv
    echo e instale as dependencias com: .venv\Scripts\python.exe -m pip install -r requirements.txt
    exit /b 1
)

if "%~1"=="" (
    echo Diga o trabalho: coletar.bat inmet, ou coletar.bat previsao
    exit /b 1
)
if not exist "logs" mkdir "logs"
set PYTHONUTF8=1

set "LOG=logs\coletor_%~1.log"
if /i "%~2"=="--extras" set "LOG=logs\coletor_%~1_extras.log"
echo [%date% %time%] coletar.py %* >> "%LOG%"
".venv\Scripts\python.exe" coletar.py %* >> "%LOG%" 2>&1

REM Devolve o codigo de saida do coletor: 1 quando o banco ou alguma consulta falharam, e o
REM Agendador mostra a execucao como falha
exit /b %ERRORLEVEL%
