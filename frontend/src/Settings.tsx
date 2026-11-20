import type { InvenTreePluginContext } from '@inventreedb/ui';
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  FileInput,
  Group,
  Loader,
  Stack,
  Table,
  Text,
  Textarea,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useCallback, useEffect, useState } from 'react';

import { API_ROOT, errorMessage, LONG_TIMEOUT } from './api';

type Mappings = {
  categories: {
    pk: number;
    supplier: string;
    supplier_category: string;
    category: number;
    category_path: string;
    function_filter: boolean;
    pending: boolean;
  }[];
  scopes: { pk: number; name: string; parents: string[]; mappings: Record<string, string[]> }[];
  units: { pk: number; name: string; units: string }[];
};

/**
 * Default mappings shipped with the plugin (Ki-nTree defaults + Farnell names).
 */
function DefaultMappings({ context, onDone }: { context: InvenTreePluginContext; onDone: () => void }) {
  const [replace, setReplace] = useState<boolean>(false);
  const [createCategories, setCreateCategories] = useState<boolean>(false);
  const [busy, setBusy] = useState<boolean>(false);
  const [summary, setSummary] = useState<any>(null);

  const load = useCallback(() => {
    setBusy(true);
    context.api
      .post(
        `${API_ROOT}/mappings/defaults/`,
        { replace, create_categories: createCategories },
        { timeout: LONG_TIMEOUT },
      )
      .then((response) => {
        setSummary(response.data);
        onDone();
      })
      .catch((err) => notifications.show({ title: 'Default mappings', message: errorMessage(err), color: 'red' }))
      .finally(() => setBusy(false));
  }, [context.api, replace, createCategories, onDone]);

  return (
    <Stack gap='xs'>
      <Title order={5}>Default mappings</Title>
      <Text size='sm'>
        The plugin includes the Ki-nTree default mappings (DigiKey categories, parameters and units, with the Farnell
        parameter names). They are loaded automatically on the first use, when the tables are empty. Load them again to
        add the rows you do not have, or to start over.
      </Text>
      <Group align='flex-end'>
        <Checkbox
          label='Replace the existing rows (your changes are lost)'
          checked={replace}
          onChange={(e) => setReplace(e.currentTarget.checked)}
        />
        <Checkbox
          label='Create the missing InvenTree categories'
          checked={createCategories}
          onChange={(e) => setCreateCategories(e.currentTarget.checked)}
        />
        <Button onClick={load} loading={busy} color={replace ? 'red' : undefined}>
          Load default mappings
        </Button>
      </Group>
      {summary && <ImportSummary summary={summary} />}
    </Stack>
  );
}

function ImportSummary({ summary }: { summary: any }) {
  return (
    <Alert color='green' title='Imported'>
      <Text size='sm'>
        {summary.categories} category mappings, {summary.scopes} parameter scopes, {summary.units} units
        {summary.categories_created ? `, ${summary.categories_created} categories created` : ''}.
      </Text>
      {summary.categories_missing.length > 0 && (
        <Text size='sm'>
          Pending (the category is created on the first import which uses it, or link it by creating it in InvenTree):{' '}
          {summary.categories_missing.join(', ')}
        </Text>
      )}
    </Alert>
  );
}

/**
 * Import of the Ki-nTree configuration files.
 */
function KintreeImport({ context, onDone }: { context: InvenTreePluginContext; onDone: () => void }) {
  const [files, setFiles] = useState<File[]>([]);
  const [replace, setReplace] = useState<boolean>(false);
  const [busy, setBusy] = useState<boolean>(false);
  const [summary, setSummary] = useState<any>(null);

  const upload = useCallback(async () => {
    setBusy(true);

    try {
      const contents: Record<string, string> = {};

      for (const file of files) {
        contents[file.name] = await file.text();
      }

      const response = await context.api.post(
        `${API_ROOT}/mappings/`,
        { files: contents, replace },
        { timeout: LONG_TIMEOUT },
      );
      setSummary(response.data);
      onDone();
    } catch (err) {
      notifications.show({ title: 'Ki-nTree import', message: errorMessage(err), color: 'red' });
    } finally {
      setBusy(false);
    }
  }, [context.api, files, replace, onDone]);

  return (
    <Stack gap='xs'>
      <Title order={5}>Import from Ki-nTree</Title>
      <Text size='sm'>
        Files from the Ki-nTree user folder (~/kintree/user): digikey_categories.yaml, supplier_parameters.yaml,
        parameters.yaml.
      </Text>
      <Group align='flex-end'>
        <FileInput
          multiple
          accept='.yaml,.yml'
          placeholder='Select the files'
          value={files}
          onChange={setFiles}
          style={{ flex: 1 }}
        />
        <Checkbox
          label='Replace the existing rows'
          checked={replace}
          onChange={(e) => setReplace(e.currentTarget.checked)}
        />
        <Button onClick={upload} loading={busy} disabled={!files.length}>
          Import
        </Button>
      </Group>
      {summary && <ImportSummary summary={summary} />}
    </Stack>
  );
}

function ScopeEditor({
  context,
  scope,
  onSaved,
}: {
  context: InvenTreePluginContext;
  scope: Mappings['scopes'][number];
  onSaved: () => void;
}) {
  const [text, setText] = useState<string>(JSON.stringify({ parent: scope.parents, ...scope.mappings }, null, 1));

  const save = useCallback(() => {
    let data: any;

    try {
      data = JSON.parse(text);
    } catch (err) {
      notifications.show({ title: scope.name, message: `Invalid JSON: ${err}`, color: 'red' });
      return;
    }

    const { parent, ...mappings } = data;
    context.api
      .put(`${API_ROOT}/mappings/scope/${scope.pk}/`, { name: scope.name, parents: parent ?? [], mappings })
      .then(() => {
        notifications.show({ title: scope.name, message: 'Saved', color: 'green' });
        onSaved();
      })
      .catch((err) => notifications.show({ title: scope.name, message: errorMessage(err), color: 'red' }));
  }, [context.api, scope, text, onSaved]);

  return (
    <Stack gap={4}>
      <Textarea
        value={text}
        onChange={(e) => setText(e.currentTarget.value)}
        autosize
        minRows={3}
        maxRows={15}
        ff='monospace'
      />
      <Group justify='flex-end'>
        <Button size='xs' onClick={save}>
          Save
        </Button>
      </Group>
    </Stack>
  );
}

type UnitsPlan = {
  applied: boolean;
  checked: number;
  changes: number;
  failures: number;
  change_samples: { part: number; part_name: string; template: string; units: string; old: string; new: string }[];
  failure_samples: { part: number; part_name: string; template: string; units: string; old: string }[];
};

/**
 * Conversion of the existing parameter values to the template units ('25V' with units V -> '25').
 */
function ParameterUnits({ context }: { context: InvenTreePluginContext }) {
  const [plan, setPlan] = useState<UnitsPlan | null>(null);
  const [busy, setBusy] = useState<boolean>(false);

  const run = useCallback(
    (apply: boolean) => {
      setBusy(true);
      const request = apply
        ? context.api.post(`${API_ROOT}/units/`, {}, { timeout: 300000 })
        : context.api.get(`${API_ROOT}/units/`, { timeout: 300000 });
      request
        .then((response) => {
          const data = response.data;

          // An HTML page instead of the data: the server does not know this endpoint yet
          if (!data || typeof data !== 'object' || !Array.isArray(data.change_samples)) {
            throw new Error(
              typeof data === 'string'
                ? 'Unexpected answer from the server: update the plugin on the server and restart it'
                : (data?.error ?? data?.detail ?? 'Unexpected answer from the server'),
            );
          }

          setPlan(data);
        })
        .catch((err) => {
          setPlan(null);
          notifications.show({ title: 'Units', message: errorMessage(err), color: 'red', autoClose: false });
        })
        .finally(() => setBusy(false));
    },
    [context.api],
  );

  return (
    <Stack gap='xs'>
      <Title order={5}>Parameter units</Title>
      <Text size='sm'>
        For templates with units, InvenTree shows the units in their own column: the values are stored as numbers in the
        template units ("25V" with units V becomes "25", "-55~85°C" with °C becomes "-55~85"). Templates without units
        keep the unit in the value, without space ("22 µF" becomes "22µF"). New imports are already saved this way.
      </Text>
      <Group>
        <Button size='xs' variant='default' onClick={() => run(false)} loading={busy && !plan?.applied}>
          Check
        </Button>
        {plan && !plan.applied && plan.changes > 0 && (
          <Button size='xs' color='orange' onClick={() => run(true)} loading={busy}>
            Convert {plan.changes} values
          </Button>
        )}
      </Group>
      {plan && (
        <Alert color={plan.applied ? 'green' : 'blue'}>
          <Text size='sm'>
            {plan.checked} parameters checked: {plan.changes} {plan.applied ? 'converted' : 'to convert'},{' '}
            {plan.failures} not convertible (left unchanged).
          </Text>
        </Alert>
      )}
      {plan && (plan.change_samples?.length ?? 0) > 0 && (
        <Table striped>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Part</Table.Th>
              <Table.Th>Template</Table.Th>
              <Table.Th>Units</Table.Th>
              <Table.Th>Value</Table.Th>
              <Table.Th>{plan.applied ? 'Converted' : 'Will be'}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {plan.change_samples.map((r, i) => (
              <Table.Tr key={`c-${i}`}>
                <Table.Td>{r.part_name}</Table.Td>
                <Table.Td>{r.template}</Table.Td>
                <Table.Td>{r.units}</Table.Td>
                <Table.Td>{r.old}</Table.Td>
                <Table.Td>{r.new}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      {plan && (plan.failure_samples?.length ?? 0) > 0 && (
        <>
          <Text size='sm' fw={500}>
            Not convertible (to fix by hand, if needed):
          </Text>
          <Table striped>
            <Table.Tbody>
              {plan.failure_samples.map((r, i) => (
                <Table.Tr key={`f-${i}`}>
                  <Table.Td>{r.part_name}</Table.Td>
                  <Table.Td>{r.template}</Table.Td>
                  <Table.Td>{r.units}</Table.Td>
                  <Table.Td>{r.old}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </>
      )}
    </Stack>
  );
}

function ImportSettings({ context }: { context: InvenTreePluginContext }) {
  const [data, setData] = useState<Mappings | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  const load = useCallback(() => {
    context.api
      .get(`${API_ROOT}/mappings/`, { timeout: LONG_TIMEOUT })
      .then((response) => setData(response.data))
      .catch((err) => notifications.show({ title: 'Mappings', message: errorMessage(err), color: 'red' }));
  }, [context.api]);

  useEffect(() => {
    load();
  }, [load]);

  const deleteCategory = (pk: number) => context.api.delete(`${API_ROOT}/mappings/category/${pk}/`).then(load);

  if (!data) {
    return <Loader />;
  }

  return (
    <Stack gap='lg'>
      <DefaultMappings context={context} onDone={load} />
      <KintreeImport context={context} onDone={load} />

      <ParameterUnits context={context} />

      <Stack gap='xs'>
        <Title order={5}>
          Parameter mappings ({data.scopes.length} scopes, {data.units.length} units)
        </Title>
        <Text size='sm'>
          Same structure as Ki-nTree supplier_parameters.yaml: {'{"InvenTree template": ["supplier name", ...]}'}, plus
          "parent". The Farnell names are in the language of the store (e.g. "Induttanza").
        </Text>
        <Table>
          <Table.Tbody>
            {data.scopes.map((scope) => (
              <Table.Tr key={scope.pk}>
                <Table.Td style={{ verticalAlign: 'top', width: 200 }}>
                  <Button variant='subtle' size='xs' onClick={() => setOpen(open === scope.pk ? null : scope.pk)}>
                    {scope.name}
                  </Button>
                  {scope.parents.map((p) => (
                    <Badge key={p} size='xs' variant='light' ml={4}>
                      {p}
                    </Badge>
                  ))}
                </Table.Td>
                <Table.Td>
                  {open === scope.pk ? (
                    <ScopeEditor context={context} scope={scope} onSaved={load} />
                  ) : (
                    <Text size='xs' c='dimmed'>
                      {Object.keys(scope.mappings).join(', ')}
                    </Text>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Stack>

      <Stack gap='xs'>
        <Title order={5}>Category mappings ({data.categories.length})</Title>
        <Text size='sm'>
          New mappings are also added from the import page, when a category is chosen with "Remember".
        </Text>
        <Table striped>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Supplier</Table.Th>
              <Table.Th>Supplier category</Table.Th>
              <Table.Th>InvenTree category</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {data.categories.map((c) => (
              <Table.Tr key={c.pk}>
                <Table.Td>{c.supplier || 'all'}</Table.Td>
                <Table.Td>{c.supplier_category}</Table.Td>
                <Table.Td>
                  {c.category_path}{' '}
                  {c.pending && (
                    <Badge
                      size='xs'
                      color='yellow'
                      variant='light'
                      title='The category does not exist yet: it is created on the first import which uses it'
                    >
                      pending
                    </Badge>
                  )}{' '}
                  {c.function_filter && (
                    <Badge size='xs' variant='light' title='Chosen by the "Function Type" parameter (Ki-nTree "__")'>
                      by function
                    </Badge>
                  )}
                </Table.Td>
                <Table.Td>
                  <Button size='xs' variant='subtle' color='red' onClick={() => deleteCategory(c.pk)}>
                    Delete
                  </Button>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Stack>
    </Stack>
  );
}

// Called by InvenTree to render the custom section of the plugin settings page
export function renderImportSettings(context: InvenTreePluginContext) {
  return <ImportSettings context={context} />;
}
