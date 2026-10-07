@echo off
REM Sobe o Painel Meteorologico no servidor Windows da unidade.
REM
REM Feito para o Agendador de Tarefas do Windows: disparo "Ao iniciar o computador", com
REM "Executar estando o usuario conectado ou nao" e "Se a tarefa falhar, reiniciar a cada
REM 1 minuto". Tambem roda com dois cliques, para testar.
REM
REM A porta padrao e 8501. Para usar outra, defina a variavel PORTA antes de chamar este
REM arquivo. A saida do Streamlit vai para logs\painel.log, porque pelo Agendador ninguem ve
REM a janela.
REM
REM O observador de arquivos do Streamlit fica desligado de proposito. Ligado, ele rele o
REM explorador.py quando o arquivo muda, mas nao os modulos que ele importa: depois de uma
REM atualizacao, o painel rodaria metade codigo novo e metade velho, e cairia com
REM AttributeError. Desligado, o painel so muda quando a tarefa e reiniciada, que e o passo de
REM atualizacao descrito em docs\guia_de_manutencao.md.

setlocal
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo Ambiente virtual nao encontrado em %CD%\.venv
    echo Crie com: py -3.14 -m venv .venv
    echo e instale as dependencias com: .venv\Scripts\python.exe -m pip install -r app\requirements.txt
    exit /b 1
)

if "%PORTA%"=="" set PORTA=8501
if not exist "logs" mkdir "logs"
set PYTHONUTF8=1

echo [%date% %time%] Subindo o painel na porta %PORTA% >> "logs\painel.log"
".venv\Scripts\python.exe" -m streamlit run app\explorador.py ^
    --server.port %PORTA% ^
    --server.address 0.0.0.0 ^
    --server.headless true ^
    --server.fileWatcherType none ^
    --browser.gatherUsageStats false >> "logs\painel.log" 2>&1

REM Devolve o codigo de saida do Streamlit: e por ele que o Agendador sabe que a tarefa falhou
REM e tenta de novo.
exit /b %ERRORLEVEL%
