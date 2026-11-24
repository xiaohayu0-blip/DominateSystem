export function displayAssistantName(text: string): string {
  return text.replace(/小八(?!爪鱼)/g, '管小智')
}

export function plainAnswer(text: string): string {
  const literals: string[] = []
  function preserve(value: string) {
    return '\uE000' + (literals.push(value) - 1) + '\uE001'
  }

  const display = text.replace(/\r\n?/g, '\n')
    .replace(/^[ \t]{0,3}(\x60{3,}|~{3,})[^\n]*\n([\s\S]*?)^[ \t]{0,3}\1[ \t]*$/gm, (_, _fence, code: string) => preserve(code.trimEnd()))
    .replace(/\\([\\*\x60_~#>\[\]()!|])/g, (_, literal: string) => preserve(literal))
    .replace(/(\x60+)(?!\x60)([^\n]+?)\1(?!\x60)/g, (_, _delimiter, code: string) => preserve(code))
    .replace(/!?\[([^\]\n]+)\]\((https?:\/\/(?:[^\s()]|\([^\s()]*\))+)\)/g, (_, label: string, url: string) =>
      label === url ? preserve(url) : label + '（' + preserve(url) + '）')
    .replace(/https?:\/\/[^\s<>*\uE000\uE001，。；、）】”]+/g, preserve)
    .replace(/^[ \t]{0,3}([-_*])(?:[ \t]*\1){2,}[ \t]*$/gm, '')
    .replace(/^[ \t]{0,3}#{1,6}[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$/gm, '$1')
    .replace(/^[ \t]{0,3}(?:>[ \t]?)+/gm, '')
    .replace(/^([ \t]*)[-+*][ \t]+/gm, '$1• ')
    .replace(/(\*{2,3}|_{2,3}|~~)(?=\S)([^\n]+?)(?<=\S)\1/g, '$2')
    .replace(/(?<![\w*_])([*_])(?=\S)([^\n]+?)(?<=\S)\1(?![\w*_])/g, '$2')

  const lines = display.split('\n')
  function tableCells(line: string) {
    return line.trim().replace(/^\||\|$/g, '').split('|').map(cell => cell.trim())
  }
  for (let index = 1; index < lines.length; index++) {
    if (!lines[index - 1].includes('|') || !/^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$/.test(lines[index])) continue
    lines[index - 1] = tableCells(lines[index - 1]).join(' · ')
    lines[index] = ''
    while (index + 1 < lines.length && lines[index + 1].includes('|')) {
      index++
      lines[index] = tableCells(lines[index]).join(' · ')
    }
  }
  return displayAssistantName(lines.join('\n')).replace(/\uE000(\d+)\uE001/g, (match, index: string) => literals[Number(index)] ?? match)
}
