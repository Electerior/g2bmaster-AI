/**
 * 프롬프트 레지스트리.
 *
 * 버전은 손으로 적지 않는다(CLAUDE.md §2-4).
 * 프롬프트 텍스트 + 모델 + 디코딩 파라미터에서 해시로 파생한다.
 * 이 값이 백엔드 분석 재사용 키에 들어가므로, 출력 의미를 바꾸는 변경은
 * 반드시 여기서 자동으로 버전이 올라가야 한다.
 */

import { createHash } from 'node:crypto';

export interface Decoding {
  temperature: number;
  topP: number;
  maxTokens: number;
}

export interface PromptDef {
  /** 사람이 읽는 라벨. 버전의 접두사로만 쓰인다. */
  label: string;
  model: string;
  decoding: Decoding;
  system: string;
  /** 사용자 메시지 템플릿. 렌더러는 순수 함수여야 한다. */
  render(input: Record<string, unknown>): string;
  /** 후처리·파싱 규칙을 바꿨을 때 올린다. 출력 의미가 바뀌기 때문이다. */
  postprocessRev: number;
}

export interface RegisteredPrompt extends PromptDef {
  version: string;
}

function derive(def: PromptDef): string {
  const material = JSON.stringify({
    system: def.system,
    // 템플릿 자체를 해시에 넣기 위해 함수 소스를 사용한다.
    render: def.render.toString(),
    model: def.model,
    decoding: def.decoding,
    postprocessRev: def.postprocessRev,
  });
  const hash = createHash('sha256').update(material, 'utf8').digest('hex').slice(0, 12);
  return `${def.label}-${hash}`;
}

export class PromptRegistry {
  private readonly byId = new Map<string, RegisteredPrompt>();

  register(id: string, def: PromptDef): RegisteredPrompt {
    if (this.byId.has(id)) throw new Error(`duplicate prompt id: ${id}`);
    const registered: RegisteredPrompt = { ...def, version: derive(def) };
    this.byId.set(id, registered);
    return registered;
  }

  get(id: string): RegisteredPrompt {
    const p = this.byId.get(id);
    if (!p) throw new Error(`unknown prompt id: ${id}`);
    return p;
  }

  /**
   * GET /api/ai/prompt-version 응답.
   * 엔드포인트마다 프롬프트가 다르므로 단일 문자열이 아니라 맵으로 준다(전제 F).
   */
  versionMap(): Record<string, string> {
    return Object.fromEntries([...this.byId].map(([id, p]) => [id, p.version]));
  }
}
