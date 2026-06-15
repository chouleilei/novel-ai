from backend.prompts.critic import build_critic_system_prompt


def test_build_critic_system_prompt_uses_strict_hard_gates_by_default():
    prompt = build_critic_system_prompt()

    assert '硬门槛' in prompt
    assert 'outline_adherence < 8，直接判定 failed' in prompt


def test_build_critic_system_prompt_can_disable_hard_gates():
    prompt = build_critic_system_prompt(hard_gates_enabled=False)

    assert '不使用单项硬门槛' in prompt
    assert 'blocking_issues 仍表示必须优先修复的问题，但它们本身不会自动导致 failed' in prompt
