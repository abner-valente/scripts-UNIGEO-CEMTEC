# Cria (ou recria) no Agendador de Tarefas do Windows as quatro tarefas dos coletores do banco:
#   UNIGEO - coletor INMET           de hora em hora, aos 50 minutos   coletar.bat inmet
#   UNIGEO - coletor INMET extras    todo dia, as 03:00                coletar.bat inmet --extras
#   UNIGEO - coletor INMET revisao   todo domingo, a 01:05             coletar.bat inmet --revisar 30
#   UNIGEO - coletor previsao        todo dia, as 04:45 e as 16:45     coletar.bat previsao
#
# O INMET aos 50 minutos: as estacoes mandam a leitura da hora ao longo de uns 40 minutos. A
# revisao rebusca 30 dias, para o banco receber os valores que o INMET corrigiu depois.
# A previsao guarda as rodadas das 00Z e das 12Z, e cada uma so pode ser buscada enquanto e a mais
# recente do modelo: a das 00Z dos tres modelos, de ~03:50 (o ECMWF fica pronto) a ~06:00 (chega
# a das 06Z do ICON), horario de MS; a das 12Z, de ~15:50 a ~18:00. As 04:45 e as 16:45 caem no
# meio dessas janelas. Se o Open-Meteo falhar nesse horario, a rodada se perde.
#
# Todas com "Executar estando o usuario conectado ou nao": rodam depois de um reinicio sem ninguem
# entrar na sessao. Por isso o script pede a senha do Windows do usuario que as executa (o que
# roda o script). A senha vai direto para o Agendador; nao fica gravada em arquivo.
#
# Rode num terminal, na raiz do projeto:
#   powershell -ExecutionPolicy Bypass -File servidor\criar_tarefas.ps1
#
# As tarefas rodam o codigo desta pasta, na branch que estiver nela: trocar a branch daqui por uma
# sem o coletar.py (a main, ate a v0.4.x entrar nela) faz os coletores falharem.

$ErrorActionPreference = "Stop"
$projeto = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $PSScriptRoot "coletar.bat"
$usuario = "$env:USERDOMAIN\$env:USERNAME"

$segura = Read-Host "Senha do Windows de $usuario" -AsSecureString
$ponteiro = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($segura)
try {
    $senha = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ponteiro)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ponteiro)
}

$hoje = (Get-Date).Date
$umaHora = New-TimeSpan -Hours 1
$tarefas = @(
    @{ Nome = "UNIGEO - coletor INMET"; Argumentos = "inmet"
       Gatilho = New-ScheduledTaskTrigger -Once -At $hoje.AddMinutes(50) -RepetitionInterval $umaHora },
    @{ Nome = "UNIGEO - coletor INMET extras"; Argumentos = "inmet --extras"
       Gatilho = New-ScheduledTaskTrigger -Daily -At $hoje.AddHours(3) },
    @{ Nome = "UNIGEO - coletor INMET revisao"; Argumentos = "inmet --revisar 30"
       Gatilho = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At $hoje.AddHours(1).AddMinutes(5) },
    @{ Nome = "UNIGEO - coletor previsao"; Argumentos = "previsao"
       Gatilho = @((New-ScheduledTaskTrigger -Daily -At $hoje.AddHours(4).AddMinutes(45)),
                   (New-ScheduledTaskTrigger -Daily -At $hoje.AddHours(16).AddMinutes(45))) }
)
# Uma execucao por vez (a seguinte nao comeca se a anterior ainda roda), recupera a que a maquina
# perdeu desligada, roda na bateria, e para a que passar de uma hora (a mais longa, a revisao,
# leva ~13 min)
$opcoes = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit $umaHora

foreach ($tarefa in $tarefas) {
    $acao = New-ScheduledTaskAction -Execute "`"$bat`"" -Argument $tarefa.Argumentos -WorkingDirectory $projeto
    Register-ScheduledTask -TaskName $tarefa.Nome -Action $acao -Trigger $tarefa.Gatilho -Settings $opcoes `
        -User $usuario -Password $senha -Force | Out-Null
    Write-Host "Criada: $($tarefa.Nome)"
}
$senha = $null

Write-Host ""
Write-Host "Pronto. Os registros de cada execucao ficam em logs\coletor_*.log"
Get-ScheduledTask -TaskName "UNIGEO - *" | Get-ScheduledTaskInfo |
    Select-Object TaskName, NextRunTime | Format-Table -AutoSize
